"""
Run the SKIDNAME script as a Cloud Run Job or console entry point.
"""

import argparse
import json
import logging
import shutil
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
        if field not in df.columns:
            continue
        if field_type == "str":
            df[field] = df[field].astype("string")
            continue
        if field_type not in ("int", "float"):
            continue

        text = df[field].astype("string").fillna("").str.strip()
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


def _prepare_sheet(loader, secrets):
    """Check links, geocode the source sheet, and write results back without loading AGOL."""
    module_logger = logging.getLogger(config.SKID_NAME)
    df = loader.load_specific_worksheet_into_dataframe(secrets.SHEET_ID, 0)

    n_rows, n_columns = df.shape
    module_logger.info(f"Loaded sheet with {n_rows} rows and {n_columns} columns")

    df = _check_links(df, url_field="Org website", status_field="website_status")

    geocode_fields = ["x", "y", "score", "match_address", "geocode_status"]
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
    return len(geocoded_df)


def _load_sheet_for_agol(loader, sheet_id):
    """Build the spatial dataframe for AGOL from geocoding results already saved in the sheet."""
    df = loader.load_specific_worksheet_into_dataframe(sheet_id, 0)
    df = transform.DataCleaning.rename_dataframe_columns_for_agol(df)

    missing = [field for field in ("x", "y") if field not in df.columns]
    if missing:
        raise ValueError(f"Cannot publish before sheet preparation; missing geocoding fields: {missing}")

    coordinates = pd.DataFrame({field: pd.to_numeric(df[field], errors="coerce") for field in ("x", "y")})
    df[["x", "y"]] = coordinates
    spatial_df = pd.DataFrame.spatial.from_xy(df, "x", "y", sr=4326)
    return spatial_df


def _save_agol_backup(gis, backup_gdb_path, feature_layer_item_id, working_dir):
    """Upload palletjack's local pre-update file-geodatabase backup to AGOL."""
    if not backup_gdb_path.is_dir():
        return None, []

    title_prefix = f"{config.SKID_NAME} backup "
    existing_items = gis.content.search(
        f'title:"{config.SKID_NAME} backup" AND owner:{gis.users.me.username}',
        item_type="File Geodatabase",
        max_items=100,
    )
    existing_items = [item for item in existing_items if item.title.startswith(title_prefix)]

    feature_layer_item = gis.content.get(feature_layer_item_id)
    if feature_layer_item is None:
        raise RuntimeError(f"Could not find AGOL feature layer item {feature_layer_item_id}.")
    feature_folder = gis.content.folders.get(
        folder=feature_layer_item.ownerFolder,
        owner=feature_layer_item.owner,
    )
    if feature_folder is None:
        raise RuntimeError(f"Could not find the AGOL folder for feature layer item {feature_layer_item_id}.")

    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    archive_name = f"{config.SKID_NAME}-backup-{timestamp}"
    zipped_gdb_path = shutil.make_archive(
        str(working_dir / archive_name),
        "zip",
        root_dir=backup_gdb_path.parent,
        base_dir=backup_gdb_path.name,
    )

    upload_job = feature_folder.add(
        item_properties={
            "type": "File Geodatabase",
            "title": f"{title_prefix}{timestamp}",
            "snippet": f"Private recovery backup for {config.SKID_NAME}.",
            "tags": f"{config.SKID_NAME}, backup",
        },
        file=str(zipped_gdb_path),
    )
    backup_item = upload_job.result()
    if backup_item is None:
        raise RuntimeError("AGOL did not return the uploaded file-geodatabase backup item.")

    return backup_item, existing_items


def _delete_prior_agol_backups(backup_item, existing_items, module_logger):
    """Remove older backups only after the new backup has been uploaded."""
    failed_item_ids = []
    for item in existing_items:
        if item.id == backup_item.id:
            continue
        try:
            if not item.delete():
                raise RuntimeError(f"Could not remove superseded AGOL backup item {item.id}.")
        except Exception:
            failed_item_ids.append(item.id)
            module_logger.warning(
                f"Could not remove superseded AGOL backup item {item.id}; keeping it in AGOL.",
                exc_info=True,
            )

    if failed_item_ids:
        module_logger.warning(
            f"Could not remove superseded AGOL backup items: {failed_item_ids}; keeping them in AGOL."
        )


def _persist_agol_backup(gis, feature_layer_item_id, working_dir, module_logger):
    backup_item, existing_backups = _save_agol_backup(
        gis,
        working_dir / "backup.gdb",
        feature_layer_item_id,
        working_dir,
    )
    if backup_item:
        _delete_prior_agol_backups(backup_item, existing_backups, module_logger)
        module_logger.info(f"Persistent recovery backup saved as AGOL item {backup_item.title}.")
    return backup_item

def _publish_sheet(loader, secrets, working_dir):
    """Load the saved sheet data into AGOL without checking links or geocoding."""
    module_logger = logging.getLogger(config.SKID_NAME)
    df = _load_sheet_for_agol(loader, secrets.SHEET_ID)
    update_df = _conform_to_schema(df, config.AGOL_SCHEMA)
    module_logger.info(f"{len(update_df)} rows are ready to load to AGOL")

    if update_df.empty:
        return 0

    gis = arcgis.gis.GIS(config.AGOL_ORG, secrets.AGOL_USER, secrets.AGOL_PASSWORD)
    overwriter = load.ServiceUpdater(gis, secrets.FEATURE_LAYER_ITEMID, working_dir=working_dir)

    previous_df = overwriter.service.query().sdf
    previous_df = _conform_to_schema(previous_df, config.AGOL_SCHEMA).reset_index(drop=True)
    try:
        overwriter.truncate_and_load(update_df, save_old=True)
    except Exception as publish_error:
        if previous_df.empty:
            module_logger.exception(
                "AGOL publishing failed; the layer was empty, so there was no prior data to restore."
            )
            raise

        module_logger.exception("AGOL publishing failed; attempting to restore the previous feature layer data.")
        try:
            restored_count = overwriter.truncate_and_load(previous_df, save_old=False)
        except Exception as restore_error:
            module_logger.exception("Automatic restoration of the previous feature layer data failed.")
            try:
                backup_item = _persist_agol_backup(
                    gis,
                    secrets.FEATURE_LAYER_ITEMID,
                    working_dir,
                    module_logger,
                )
            except Exception as backup_error:
                module_logger.exception("Could not save the local recovery backup to AGOL.")
                raise RuntimeError(
                    "AGOL publishing failed, automatic restoration failed, and the persistent recovery backup "
                    "could not be saved."
                ) from backup_error
            recovery_item = f" Persistent AGOL backup: {backup_item.title}." if backup_item else ""
            raise RuntimeError(
                "AGOL publishing failed and automatic restoration also failed; the live layer may require manual "
                f"recovery.{recovery_item}"
            ) from restore_error

        module_logger.error(f"Restored {restored_count} previously published features after the failed update.")
        try:
            _persist_agol_backup(gis, secrets.FEATURE_LAYER_ITEMID, working_dir, module_logger)
        except Exception as backup_error:
            module_logger.exception("The previous data was restored, but its persistent backup could not be saved.")
            raise RuntimeError(
                "AGOL publishing failed and the previous feature layer data was restored, but its persistent backup "
                "could not be saved."
            ) from backup_error
        raise RuntimeError("AGOL publishing failed; the previous feature layer data was restored.") from publish_error

    try:
        _persist_agol_backup(gis, secrets.FEATURE_LAYER_ITEMID, working_dir, module_logger)
    except Exception as backup_error:
        module_logger.exception("The feature layer was updated, but its persistent recovery backup could not be saved.")
        raise RuntimeError(
            "AGOL feature layer was updated, but its persistent recovery backup could not be saved."
        ) from backup_error
    module_logger.info(f"Overwrote {len(update_df)} features to AGOL Feature Layer.")
    return len(update_df)


def process(mode=None):
    """Run sheet preparation, AGOL publishing, or both in sequence."""
    if mode is None:
        mode = _parse_mode()
    if mode not in ("all", "prepare", "publish"):
        raise ValueError(f"Unsupported mode {mode!r}; expected 'all', 'prepare', or 'publish'")

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

        credentials, _ = google_auth_default(
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
        )
        loader = extract.GSheetLoader(credentials)

        prepared_count = None
        updated_count = None
        if mode in ("all", "prepare"):
            module_logger.info("Preparing Google Sheet data")
            prepared_count = _prepare_sheet(loader, secrets)
        if mode in ("all", "publish"):
            module_logger.info("Publishing saved Google Sheet data to AGOL")
            updated_count = _publish_sheet(loader, secrets, tempdir_path)

        end = datetime.now(UTC)

        summary_message = MessageDetails()
        summary_message.subject = {
            "all": "Update Summary",
            "prepare": "Sheet Preparation Summary",
            "publish": "AGOL Publish Summary",
        }[mode]
        summary_rows = [
            f"{config.SKID_NAME} update {start.strftime('%Y-%m-%d')}",
            "=" * 20,
            "",
            f"Start time: {start.strftime('%H:%M:%S')}",
            f"End time: {end.strftime('%H:%M:%S')}",
            f"Duration: {end - start!s}",
        ]
        if prepared_count is not None:
            summary_rows.append(f"Rows checked and geocoded: {prepared_count}")
        if updated_count is not None:
            summary_rows.append(f"Features updated: {updated_count}")

        summary_message.message = "\n".join(summary_rows)
        summary_message.attachments = tempdir_path / log_name

        skid_supervisor.notify(summary_message)

        #: Remove file handler so the tempdir will close properly
        loggers = [logging.getLogger(config.SKID_NAME), logging.getLogger("palletjack")]
        _remove_log_file_handlers(log_name, loggers)


def _parse_mode(argv=None):
    """Parse command-line options and return the requested workflow phase."""
    parser = argparse.ArgumentParser(description="Check and geocode the Google Sheet, then optionally publish to AGOL.")
    parser.add_argument(
        "--mode",
        choices=("all", "prepare", "publish"),
        default="all",
        help="Run sheet preparation, AGOL publishing, or both (default: all)",
    )
    return parser.parse_args(argv).mode


def cli():
    """Run the workflow phase selected on the command line."""
    process(_parse_mode())


#: Putting this here means you can call the file via `python main.py` and it will run. Useful for pre-GCF testing.
if __name__ == "__main__":
    cli()
