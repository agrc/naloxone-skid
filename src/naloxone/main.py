"""
Run the SKIDNAME script as a Cloud Run Job or console entry point.
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib.parse import urlparse

import arcgis
import pandas as pd
import requests
from google.auth import default as google_auth_default
from palletjack import extract, load, transform
from pygsheets.utils import format_addr
from supervisor.message_handlers import SendGridHandler
from supervisor.models import MessageDetails, Supervisor

from naloxone import __version__

from . import config


def _get_secrets():
    """A helper method for loading secrets from either a GCF mount point or the local src/skidname/secrets/secrets.json file

    Raises:
        FileNotFoundError: If the secrets file can't be found.

    Returns:
        dict: The secrets .json loaded as a dictionary
    """

    secret_folder = Path("/secrets")

    #: Try to get the secrets from the Cloud Function mount point
    if secret_folder.exists():
        return json.loads(Path("/secrets/app/secrets.json").read_text(encoding="utf-8"))

    #: Otherwise, try to load a local copy for local development
    secret_folder = Path(__file__).parent / "secrets"
    if secret_folder.exists():
        return json.loads((secret_folder / "secrets.json").read_text(encoding="utf-8"))

    raise FileNotFoundError("Secrets folder not found; secrets not loaded.")


def _initialize(log_path, sendgrid_api_key):
    """A helper method to set up logging and supervisor

    Args:
        log_path (Path): File path for the logfile to be written
        sendgrid_api_key (str): The API key for sendgrid for this particular application

    Returns:
        Supervisor: The supervisor object used for sending messages
    """

    skid_logger = logging.getLogger(config.SKID_NAME)
    skid_logger.setLevel(config.LOG_LEVEL)
    palletjack_logger = logging.getLogger("palletjack")
    palletjack_logger.setLevel(config.LOG_LEVEL)

    cli_handler = logging.StreamHandler(sys.stdout)
    cli_handler.setLevel(config.LOG_LEVEL)
    formatter = logging.Formatter(
        fmt="%(levelname)-7s %(asctime)s %(name)15s:%(lineno)5s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    cli_handler.setFormatter(formatter)

    log_handler = logging.FileHandler(log_path, mode="w")
    log_handler.setLevel(config.LOG_LEVEL)
    log_handler.setFormatter(formatter)

    skid_logger.addHandler(cli_handler)
    skid_logger.addHandler(log_handler)
    palletjack_logger.addHandler(cli_handler)
    palletjack_logger.addHandler(log_handler)

    #: Log any warnings at logging.WARNING
    #: Put after everything else to prevent creating a duplicate, default formatter
    #: (all log messages were duplicated if put at beginning)
    logging.captureWarnings(True)

    skid_logger.debug("Creating Supervisor object")
    skid_supervisor = Supervisor(handle_errors=False)
    sendgrid_settings = config.SENDGRID_SETTINGS
    sendgrid_settings["api_key"] = sendgrid_api_key
    skid_supervisor.add_message_handler(
        SendGridHandler(sendgrid_settings=sendgrid_settings, client_name=config.SKID_NAME, client_version=__version__)
    )

    return skid_supervisor


def _remove_log_file_handlers(log_name, loggers):
    """A helper function to remove the file handlers so the tempdir will close correctly

    Args:
        log_name (str): The log file's filename
        loggers (List<str>): The loggers that are writing to log_name
    """

    for logger in loggers:
        for handler in logger.handlers[:]:
            if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).name == log_name:
                logger.removeHandler(handler)
                handler.close()


def _check_url(session, url, timeout=10):

    url = str(url).strip()
    try:
        has_scheme = bool(urlparse(url).scheme)
    except ValueError:
        return "Failed to connect. The URL might be invalid or the site may be down."

    if not has_scheme:
        url = f"https://{url}"

    try:
        response = session.head(url, timeout=timeout, allow_redirects=True)
        #: Some servers reject HEAD but serve GET fine
        if response.status_code in (403, 405, 501):
            response = session.get(url, timeout=timeout, allow_redirects=True, stream=True)
            response.close()
    #: ConnectTimeout subclasses both Timeout and ConnectionError, so Timeout must come first
    except requests.exceptions.Timeout:
        return "Connection timed out"
    except (
        requests.exceptions.ConnectionError,
        requests.exceptions.InvalidURL,
        requests.exceptions.MissingSchema,
        requests.exceptions.InvalidSchema,
    ):
        return "Failed to connect. The URL might be invalid or the site may be down."
    except requests.exceptions.RequestException as error:
        return f"An error occurred: {error.__class__.__name__}"

    if response.status_code >= 400:
        return f"An error occurred: {response.status_code}"
    return "Valid"


def _check_links(df, url_field="Org website", status_field="website_status", max_workers=10):
    """Check the status of URLs in a DataFrame.

    Args:
        df (pd.DataFrame): The DataFrame containing the URLs.
        url_field (str): The column name containing the URLs.
        status_field (str): Column to write results to.
        max_workers (int): Number of concurrent requests. Defaults to 10.

    Returns:
        pd.DataFrame: The DataFrame with the status codes added.
    """
    module_logger = logging.getLogger(config.SKID_NAME)
    module_logger.info(f"Checking links in column '{url_field}' and writing results to '{status_field}'")

    urls = df[url_field].fillna("").astype(str).str.strip()
    unique_urls = urls.unique()
    module_logger.info(f"Checking {len(unique_urls)} unique website links")

    with requests.Session() as session, ThreadPoolExecutor(max_workers=max_workers) as executor:
        session.headers["User-Agent"] = "Mozilla/5.0 (compatible; naloxone-skid link checker)"
        results = dict(zip(unique_urls, executor.map(lambda url: _check_url(session, url), unique_urls)))

    df = df.assign(**{status_field: urls.map(results)})

    invalid = df[~df[status_field].str.startswith("Valid")]
    module_logger.info(f"{len(invalid)} rows have an invalid or missing website")
    for url, status in invalid[[url_field, status_field]].drop_duplicates().itertuples(index=False):
        module_logger.debug(f"{url}: {status}")

    return df


def _conform_to_schema(df, schema):
    """Keep only the schema fields (plus SHAPE) and cast them to the schema types.

    Values that can't be cast are set to null and logged as warnings.

    Args:
        df (pd.DataFrame): The data to be loaded.
        schema (dict): Field name -> "str", "int", or "float".

    Returns:
        pd.DataFrame: The conformed data.
    """
    module_logger = logging.getLogger(config.SKID_NAME)

    df = df.rename(columns={"matchAddress": "match_address"})

    address_fields = ("Address_1", "Address_2")
    if any(field in df.columns for field in address_fields):
        address_parts = [
            df[field].fillna("").astype(str).str.strip() if field in df.columns else pd.Series("", index=df.index)
            for field in address_fields
        ]
        df["Address"] = address_parts[0].str.cat(address_parts[1], sep=" ").str.strip()

    missing = [field for field in schema if field not in df.columns]
    if missing:
        module_logger.warning(f"Schema fields missing from new data: {missing}")

    extra = [column for column in df.columns if column not in schema and column != "SHAPE"]
    df = df.drop(columns=extra)

    for field, field_type in schema.items():
        if field_type not in ("int", "float") or field not in df.columns:
            continue

        text = df[field].where(df[field].notna(), "").astype(str).str.strip()
        blank = text.eq("")
        converted = pd.to_numeric(text.mask(blank), errors="coerce")
        if field_type == "int":
            converted = converted.mask(converted % 1 != 0)

        for index, value in text[~blank & converted.isna()].items():
            module_logger.warning(f"{field}: could not convert {value!r} on row {index} to {field_type}; set to null")

        df[field] = converted.astype("Int64") if field_type == "int" else converted.astype("float64")

    return df


def _write_columns_to_sheet(gsheets_client, sheet_id, df, fields, worksheet_index=0):
    """Write df columns to the same-named sheet columns, adding headers for any that don't exist yet.

    Existing values in those columns are overwritten; all other columns are left untouched. The df index must be the
    0-based data row from the sheet (sheet row = index + 2), as returned by GSheetLoader.

    Args:
        gsheets_client (pygsheets.Client): Authorized client, e.g. GSheetLoader.gsheets_client.
        sheet_id (str): The spreadsheet key.
        df (pd.DataFrame): Data containing the fields to write.
        fields (list[str]): Column names to write; used as the sheet header names.
        worksheet_index (int): Index of the worksheet to write to. Defaults to 0.
    """
    module_logger = logging.getLogger(config.SKID_NAME)
    worksheet = gsheets_client.open_by_key(sheet_id).worksheet("index", worksheet_index)

    header = [name.strip() for name in worksheet.get_row(1, include_tailing_empty=False)]
    header += [field for field in fields if field not in header]
    if len(header) > worksheet.cols:
        worksheet.add_cols(len(header) - worksheet.cols)

    output = df[fields].reindex(range(df.index.max() + 1)).astype(object)
    output = output.where(output.notna(), "")
    last_row = len(output) + 1

    for field in fields:
        column = header.index(field) + 1
        cell_range = f"{format_addr((1, column))}:{format_addr((last_row, column))}"
        worksheet.update_values(cell_range, [[field]] + [[value] for value in output[field].tolist()])

    red = {"red": 244 / 255, "green": 204 / 255, "blue": 204 / 255}
    yellow = {"red": 244 / 255, "green": 233 / 255, "blue": 204 / 255}
    scores = pd.to_numeric(output["score"], errors="coerce") if "score" in output else None
    format_requests = []
    for field in fields:
        if field not in ("website_status", "web_status", "match_address", "score"):
            continue

        rows = []
        for index, value in output[field].items():
            color = None
            if field in ("website_status", "web_status"):
                if value != "Valid":
                    color = red
            else:
                score = scores.loc[index] if scores is not None else float("nan")
                address = str(output.loc[index, "match_address"]) if "match_address" in output else ""
                if address.strip().casefold() == "no match" or score == 0:
                    color = red
                elif 0 < score < 90:
                    color = yellow

            cell_format = {"backgroundColorStyle": {"rgbColor": color}} if color else {}
            rows.append({"values": [{"userEnteredFormat": cell_format}]})

        if rows:
            format_requests.append(
                {
                    "updateCells": {
                        "start": {"sheetId": worksheet.id, "rowIndex": 1, "columnIndex": header.index(field)},
                        "rows": rows,
                        "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.backgroundColorStyle",
                    }
                }
            )

    if format_requests:
        gsheets_client.sheet.batch_update(sheet_id, format_requests)

    module_logger.info(f"Wrote {fields} for {len(output)} rows to worksheet '{worksheet.title}'")


def process():
    """The main function that does all the work."""

    #: Set up secrets, tempdir, supervisor, and logging
    start = datetime.now(UTC)

    secrets = SimpleNamespace(**_get_secrets())

    with TemporaryDirectory() as tempdir:
        tempdir_path = Path(tempdir)
        log_name = f"{config.LOG_FILE_NAME}_{start.strftime('%Y%m%d-%H%M%S')}.txt"
        log_path = tempdir_path / log_name

        skid_supervisor = _initialize(log_path, secrets.SENDGRID_API_KEY)
        module_logger = logging.getLogger(config.SKID_NAME)
        module_logger.info(f"Starting {config.SKID_NAME} version {__version__}")

        #: Get our GIS object via the ArcGIS API for Python
        gis = arcgis.gis.GIS(config.AGOL_ORG, secrets.AGOL_USER, secrets.AGOL_PASSWORD)

        module_logger.info("Reading in data from Google Sheets")
        credentials, _ = google_auth_default()
        loader = extract.GSheetLoader(credentials)

        df = loader.load_specific_worksheet_into_dataframe(secrets.SHEET_ID, 0)

        n_rows, n_columns = df.shape
        module_logger.info(f"Loaded sheet with {n_rows} rows and {n_columns} columns")

        #: Check website URLs
        df = _check_links(df, url_field="Org website", status_field="website_status")

        geocode_fields = ["x", "y", "score", "match_address", "geocode_status"]

        #: Geocode
        #: drop previous geocode fields if they exist
        df = df.drop(columns=[col for col in geocode_fields if col in df.columns], errors="ignore")
        module_logger.info("Dropped previous geocode fields if they existed")
        df = transform.DataCleaning.rename_dataframe_columns_for_agol(df)
        module_logger.info("Geocoding locations")
        geocoder = transform.APIGeocoder(api_key=secrets.GEOCODING_API_KEY, referrer=secrets.URL_TEMPLATE)
        geocoded_df = geocoder.geocode_dataframe(
            df,
            street_col="Address_1",
            zone_col="Zip_Code",
            wkid=4326,
            acceptScore=70,
        )

        sheet_fields = ["website_status", "x", "y", "score", "match_address"]
        _write_columns_to_sheet(
            loader.gsheets_client,
            secrets.SHEET_ID,
            geocoded_df.rename(columns={"matchAddress": "match_address"}),
            sheet_fields,
        )

        #: OVERWRITE / TRUNCATE AND LOAD DATA
        opts = geocoded_df["Opts_In_OUT"]
        update_df = geocoded_df[opts.isna() | opts.astype(str).str.strip().eq("")]
        update_df = _conform_to_schema(update_df, config.AGOL_SCHEMA)
        module_logger.info(f"{len(update_df)} rows have a blank Opts In/Out value")

        updated_count = 0
        if not update_df.empty:
            overwriter = load.ServiceUpdater(gis, secrets.FEATURE_LAYER_ITEMID, working_dir=tempdir_path)
            overwriter.truncate_and_load(update_df, save_old=True)
            updated_count = len(update_df)
            module_logger.info(
                f"Overwrote {updated_count} features to AGOL Feature Layer. Backup saved in {tempdir_path}."
            )

        end = datetime.now(UTC)

        summary_message = MessageDetails()
        summary_message.subject = "Update Summary"
        summary_rows = [
            f"{config.SKID_NAME} update {start.strftime('%Y-%m-%d')}",
            "=" * 20,
            "",
            f"Start time: {start.strftime('%H:%M:%S')}",
            f"End time: {end.strftime('%H:%M:%S')}",
            f"Duration: {end - start!s}",
            f"Features updated: {updated_count}",
            #: Add other rows here containing summary info captured/calculated during the working portion of the skid,
            #: like the number of rows updated or the number of successful attachment overwrites.
        ]

        summary_message.message = "\n".join(summary_rows)
        summary_message.attachments = tempdir_path / log_name

        skid_supervisor.notify(summary_message)

        #: Remove file handler so the tempdir will close properly
        loggers = [logging.getLogger(config.SKID_NAME), logging.getLogger("palletjack")]
        _remove_log_file_handlers(log_name, loggers)


#: Putting this here means you can call the file via `python main.py` and it will run. Useful for pre-GCF testing.
if __name__ == "__main__":
    process()
