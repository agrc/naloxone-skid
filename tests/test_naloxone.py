from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from naloxone import main


def test_get_secrets_from_gcp_location(mocker):
    mocker.patch("pathlib.Path.exists", return_value=True)
    mocker.patch("pathlib.Path.read_text", return_value='{"foo":"bar"}')

    secrets = main._get_secrets()

    assert secrets == {"foo": "bar"}


def test_get_secrets_from_local_location(mocker):
    exists_mock = mocker.Mock(side_effect=[False, True])
    mocker.patch("pathlib.Path.exists", new=exists_mock)
    mocker.patch("pathlib.Path.read_text", return_value='{"foo":"bar"}')

    secrets = main._get_secrets()

    assert secrets == {"foo": "bar"}
    assert exists_mock.call_count == 2


def test_check_url_adds_https_and_returns_valid(mocker):
    session = mocker.Mock()
    session.head.return_value.status_code = 200

    result = main._check_url(session, "  example.com  ", timeout=5)

    assert result == "Valid"
    session.head.assert_called_once_with("https://example.com", timeout=5, allow_redirects=True)
    session.get.assert_not_called()


@pytest.mark.parametrize("status_code", [403, 405, 501])
def test_check_url_falls_back_to_get_for_rejected_head(status_code, mocker):
    session = mocker.Mock()
    session.head.return_value.status_code = status_code
    response = session.get.return_value
    response.status_code = 200

    result = main._check_url(session, "https://example.com")

    assert result == "Valid"
    session.get.assert_called_once_with("https://example.com", timeout=10, allow_redirects=True, stream=True)
    response.close.assert_called_once()


def test_check_url_returns_http_error_status(mocker):
    session = mocker.Mock()
    session.head.return_value.status_code = 404

    result = main._check_url(session, "https://example.com")

    assert result == "An error occurred: 404"
    session.get.assert_not_called()


def test_check_url_returns_invalid_url_for_malformed_bracket(mocker):
    session = mocker.Mock()

    result = main._check_url(session, "https://[broken")

    assert result == "Failed to connect. The URL might be invalid or the site may be down."
    session.head.assert_not_called()


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (requests.exceptions.Timeout(), "Connection timed out"),
        (
            requests.exceptions.ConnectionError(),
            "Failed to connect. The URL might be invalid or the site may be down.",
        ),
        (requests.exceptions.RequestException(), "An error occurred: RequestException"),
    ],
)
def test_check_url_reports_request_errors(exception, expected, mocker):
    session = mocker.Mock()
    session.head.side_effect = exception

    assert main._check_url(session, "https://example.com") == expected


def test_check_links_checks_unique_normalized_urls_and_adds_statuses(mocker):
    df = pd.DataFrame({"Org website": [" https://one.example ", "https://one.example", None, "two.example"]})
    check_url = mocker.patch.object(
        main,
        "_check_url",
        side_effect=lambda session, url: {
            "https://one.example": "Valid",
            "": "An error occurred: 404",
            "two.example": "Valid",
        }[url],
    )

    result = main._check_links(df, max_workers=1)

    assert result["website_status"].tolist() == ["Valid", "Valid", "An error occurred: 404", "Valid"]
    assert check_url.call_count == 3
    assert [call.args[1] for call in check_url.call_args_list] == ["https://one.example", "", "two.example"]
    assert "website_status" not in df.columns


def test_conform_to_schema_keeps_every_record_and_drops_unmapped_columns():
    df = pd.DataFrame(
        {
            "Organization_Name": ["Organization 1", "Organization 2"],
            "unmapped_column": ["value 1", "value 2"],
        }
    )

    result = main._conform_to_schema(df, main.config.AGOL_SCHEMA)

    assert result["Organization_Name"].tolist() == ["Organization 1", "Organization 2"]
    assert len(result) == len(df)
    assert "unmapped_column" not in result.columns


def test_load_sheet_for_agol_uses_saved_coordinates(mocker):
    source = pd.DataFrame({"x": ["-111.94"], "y": ["40.77"]})
    loader = mocker.Mock()
    loader.load_specific_worksheet_into_dataframe.return_value = source
    mocker.patch.object(main.transform.DataCleaning, "rename_dataframe_columns_for_agol", side_effect=lambda df: df)
    spatial_result = pd.DataFrame({"SHAPE": ["point"]})
    from_xy = mocker.Mock(return_value=spatial_result)
    mocker.patch.object(pd.DataFrame, "spatial", SimpleNamespace(from_xy=from_xy))

    result = main._load_sheet_for_agol(loader, "sheet-id")

    assert result is spatial_result
    from_xy.assert_called_once()
    spatial_df = from_xy.call_args.args[0]
    assert spatial_df[["x", "y"]].iloc[0].tolist() == [-111.94, 40.77]
    assert from_xy.call_args.args[1:] == ("x", "y")
    assert from_xy.call_args.kwargs == {"sr": 4326}


def test_load_sheet_for_agol_publishes_rows_without_valid_geometry(mocker):
    source = pd.DataFrame(
        {
            "Organization_Name": ["Origin", "Blank", "Non-numeric", "Out of range", "Valid"],
            "x": ["0", "", "invalid", "181", "-111.94"],
            "y": ["0", "40.77", "40.77", "40.77", "40.77"],
        }
    )
    loader = mocker.Mock()
    loader.load_specific_worksheet_into_dataframe.return_value = source
    mocker.patch.object(main.transform.DataCleaning, "rename_dataframe_columns_for_agol", side_effect=lambda df: df)

    def from_xy(df, x_column, y_column, sr):
        result = df.copy()
        result["SHAPE"] = list(zip(result[x_column], result[y_column]))
        return result

    mocker.patch.object(pd.DataFrame, "spatial", SimpleNamespace(from_xy=from_xy))

    result = main._load_sheet_for_agol(loader, "sheet-id")

    assert result["Organization_Name"].tolist() == source["Organization_Name"].tolist()
    assert result["SHAPE"].iloc[:4].tolist() == [None] * 4
    assert result["SHAPE"].iloc[4] == (-111.94, 40.77)


def test_load_sheet_for_agol_requires_prepared_coordinates(mocker):
    loader = mocker.Mock()
    loader.load_specific_worksheet_into_dataframe.return_value = pd.DataFrame({"Organization_Name": ["Organization"]})
    mocker.patch.object(main.transform.DataCleaning, "rename_dataframe_columns_for_agol", side_effect=lambda df: df)

    with pytest.raises(ValueError, match="missing geocoding fields"):
        main._load_sheet_for_agol(loader, "sheet-id")


@pytest.mark.parametrize(
    ("argv", "expected_mode"),
    [
        (["naloxone"], "all"),
        (["naloxone", "--mode", "prepare"], "prepare"),
        (["naloxone", "--mode", "publish"], "publish"),
    ],
)
def test_cli_runs_requested_mode(argv, expected_mode, mocker, monkeypatch):
    process = mocker.patch.object(main, "process")
    monkeypatch.setattr(main.sys, "argv", argv)

    main.cli()

    process.assert_called_once_with(expected_mode)


def test_process_without_mode_parses_command_line(mocker):
    parse_mode = mocker.patch.object(main, "_parse_mode", return_value="invalid")

    with pytest.raises(ValueError, match="Unsupported mode"):
        main.process()

    parse_mode.assert_called_once_with()


def test_geocode_sample_address(mocker):
    validate_api_key = mocker.patch.object(main.transform.utils.Geocoding, "validate_api_key")
    geocode_addr = mocker.patch.object(
        main.transform.utils.Geocoding,
        "geocode_addr",
        return_value=(-111.94, 40.77, 100, "1594 W North Temple, Salt Lake City, UT 84116"),
    )
    geocoder = main.transform.APIGeocoder(api_key="test-api-key", referrer="https://example.com")
    address = pd.DataFrame({"Address_1": ["1594 W North Temple"], "Zip_Code": ["84116"]})

    result = geocoder.geocode_dataframe(
        address,
        street_col="Address_1",
        zone_col="Zip_Code",
        wkid=4326,
        acceptScore=70,
    )

    validate_api_key.assert_called_once_with("test-api-key", referrer="https://example.com")
    geocode_addr.assert_called_once_with(
        "1594 W North Temple",
        "84116",
        "test-api-key",
        (0.015, 0.03),
        spatialReference="4326",
        referrer="https://example.com",
        acceptScore=70,
    )
    assert result.loc[0, "x"] == -111.94
    assert result.loc[0, "y"] == 40.77
    assert result.loc[0, "score"] == 100
    assert result.loc[0, "matchAddress"] == "1594 W North Temple, Salt Lake City, UT 84116"
