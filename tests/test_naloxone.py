from pathlib import Path
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


def test_conform_to_schema_handles_nullable_integer_columns():
    df = pd.DataFrame({"Zip_Code": pd.Series([pd.NA, 84101], dtype="Int32")})

    result = main._conform_to_schema(df, {"Zip_Code": "int"})

    assert pd.isna(result["Zip_Code"].iloc[0])
    assert result["Zip_Code"].iloc[1] == 84101


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


def test_load_sheet_for_agol_does_not_validate_or_rewrite_coordinates(mocker):
    source = pd.DataFrame(
        {
            "Organization_Name": ["Origin", "Out of range", "Non-numeric"],
            "x": ["0", "181", "invalid"],
            "y": ["0", "40.77", "40.77"],
        }
    )
    loader = mocker.Mock()
    loader.load_specific_worksheet_into_dataframe.return_value = source
    mocker.patch.object(main.transform.DataCleaning, "rename_dataframe_columns_for_agol", side_effect=lambda df: df)

    def from_xy(df, x_column, y_column, sr):
        result = df.copy()
        result["SHAPE"] = list(zip(result[x_column], result[y_column]))
        return result

    from_xy_mock = mocker.Mock(side_effect=from_xy)
    mocker.patch.object(pd.DataFrame, "spatial", SimpleNamespace(from_xy=from_xy_mock))

    result = main._load_sheet_for_agol(loader, "sheet-id")

    assert result["Organization_Name"].tolist() == source["Organization_Name"].tolist()
    coordinates = from_xy_mock.call_args.args[0][["x", "y"]]
    assert coordinates.iloc[:2].values.tolist() == [[0.0, 0.0], [181.0, 40.77]]
    assert pd.isna(coordinates["x"].iloc[2])


def test_load_sheet_for_agol_requires_prepared_coordinates(mocker):
    loader = mocker.Mock()
    loader.load_specific_worksheet_into_dataframe.return_value = pd.DataFrame({"Organization_Name": ["Organization"]})
    mocker.patch.object(main.transform.DataCleaning, "rename_dataframe_columns_for_agol", side_effect=lambda df: df)

    with pytest.raises(ValueError, match="missing geocoding fields"):
        main._load_sheet_for_agol(loader, "sheet-id")


def test_save_agol_backup_uploads_local_geodatabase_to_feature_layer_folder(mocker, tmp_path):
    backup_gdb_path = tmp_path / "backup.gdb"
    backup_gdb_path.mkdir()
    gis = mocker.Mock()
    gis.users.me.username = "user"
    existing_item = mocker.Mock()
    existing_item.title = f"{main.config.SKID_NAME} backup old"
    gis.content.search.return_value = [existing_item]
    feature_layer_item = mocker.Mock(ownerFolder="feature-folder-id", owner="user")
    gis.content.get.return_value = feature_layer_item
    new_item = mocker.Mock()
    feature_folder = gis.content.folders.get.return_value
    feature_folder.add.return_value.result.return_value = new_item
    archive_path = str(tmp_path / "naloxone-skid-backup.zip")
    make_archive = mocker.patch.object(main.shutil, "make_archive", return_value=archive_path)

    backup_item, existing_items = main._save_agol_backup(
        gis, backup_gdb_path, "feature-layer-id", tmp_path
    )

    assert backup_item is new_item
    assert existing_items == [existing_item]
    gis.content.get.assert_called_once_with("feature-layer-id")
    gis.content.folders.get.assert_called_once_with(folder="feature-folder-id", owner="user")
    make_archive.assert_called_once()
    archive_args = make_archive.call_args
    assert Path(archive_args.args[0]).parent == tmp_path
    assert Path(archive_args.args[0]).name.startswith(f"{main.config.SKID_NAME}-backup-")
    assert archive_args.args[1:] == ("zip",)
    assert archive_args.kwargs == {"root_dir": tmp_path, "base_dir": "backup.gdb"}
    upload_args = feature_folder.add.call_args.kwargs
    assert upload_args["item_properties"]["type"] == "File Geodatabase"
    assert upload_args["item_properties"]["title"].startswith(f"{main.config.SKID_NAME} backup ")
    assert upload_args["file"] == archive_path


def test_save_agol_backup_skips_missing_local_geodatabase(mocker, tmp_path):
    gis = mocker.Mock()

    backup_item, existing_items = main._save_agol_backup(
        gis, tmp_path / "backup.gdb", "feature-layer-id", tmp_path
    )

    assert backup_item is None
    assert existing_items == []
    gis.content.search.assert_not_called()


def test_publish_sheet_restores_previous_data_when_update_fails(mocker, tmp_path):
    loader = mocker.Mock()
    secrets = SimpleNamespace(
        SHEET_ID="sheet-id", AGOL_USER="user", AGOL_PASSWORD="password", FEATURE_LAYER_ITEMID="item-id"
    )
    new_data = pd.DataFrame({"Organization_Name": ["New"]})
    previous_data = pd.DataFrame({"Organization_Name": ["Existing"]})
    mocker.patch.object(main, "_load_sheet_for_agol", return_value=new_data)
    mocker.patch.object(main, "_conform_to_schema", side_effect=lambda df, schema: df.copy())
    mocker.patch.object(main.arcgis.gis, "GIS")
    backup_item = mocker.Mock(title="persistent backup")
    save_backup = mocker.patch.object(main, "_save_agol_backup", return_value=(backup_item, []))
    delete_backups = mocker.patch.object(main, "_delete_prior_agol_backups")
    updater = mocker.Mock()
    updater.service.query.return_value.sdf = previous_data
    updater.truncate_and_load.side_effect = [RuntimeError("append failed"), 1]
    mocker.patch.object(main.load, "ServiceUpdater", return_value=updater)

    with pytest.raises(RuntimeError, match="previous feature layer data was restored"):
        main._publish_sheet(loader, secrets, tmp_path)

    assert updater.truncate_and_load.call_count == 2
    assert updater.truncate_and_load.call_args_list[0].args[0].equals(new_data)
    assert updater.truncate_and_load.call_args_list[0].kwargs == {"save_old": True}
    assert updater.truncate_and_load.call_args_list[1].args[0].equals(previous_data)
    assert updater.truncate_and_load.call_args_list[1].kwargs == {"save_old": False}
    save_backup.assert_called_once()
    delete_backups.assert_called_once()
    assert delete_backups.call_args.args[:2] == (backup_item, [])


def test_publish_sheet_reports_when_restoring_previous_data_fails(mocker, tmp_path):
    secrets = SimpleNamespace(
        SHEET_ID="sheet-id", AGOL_USER="user", AGOL_PASSWORD="password", FEATURE_LAYER_ITEMID="item-id"
    )
    mocker.patch.object(main, "_load_sheet_for_agol", return_value=pd.DataFrame({"Organization_Name": ["New"]}))
    mocker.patch.object(main, "_conform_to_schema", side_effect=lambda df, schema: df.copy())
    mocker.patch.object(main.arcgis.gis, "GIS")
    backup_item = mocker.Mock(title="persistent backup")
    mocker.patch.object(main, "_save_agol_backup", return_value=(backup_item, []))
    updater = mocker.Mock()
    updater.service.query.return_value.sdf = pd.DataFrame({"Organization_Name": ["Existing"]})
    updater.truncate_and_load.side_effect = [RuntimeError("append failed"), RuntimeError("restore failed")]
    mocker.patch.object(main.load, "ServiceUpdater", return_value=updater)

    with pytest.raises(RuntimeError, match="automatic restoration also failed.*persistent backup"):
        main._publish_sheet(mocker.Mock(), secrets, tmp_path)

    assert updater.truncate_and_load.call_count == 2


def test_publish_sheet_replaces_old_backup_after_success(mocker, tmp_path):
    secrets = SimpleNamespace(
        SHEET_ID="sheet-id", AGOL_USER="user", AGOL_PASSWORD="password", FEATURE_LAYER_ITEMID="item-id"
    )
    mocker.patch.object(main, "_load_sheet_for_agol", return_value=pd.DataFrame({"Organization_Name": ["New"]}))
    mocker.patch.object(main, "_conform_to_schema", side_effect=lambda df, schema: df.copy())
    mocker.patch.object(main.arcgis.gis, "GIS")
    backup_item = mocker.Mock(title="latest backup", id="new")
    old_item = mocker.Mock(id="old")
    old_item.delete.return_value = True
    events = []
    save_backup = mocker.patch.object(main, "_save_agol_backup", return_value=(backup_item, [old_item]))
    save_backup.side_effect = lambda *args: (events.append("backup") or backup_item, [old_item])
    updater = mocker.Mock()
    updater.service.query.return_value.sdf = pd.DataFrame({"Organization_Name": ["Existing"]})
    updater.truncate_and_load.side_effect = lambda *args, **kwargs: events.append("truncate") or 1
    mocker.patch.object(main.load, "ServiceUpdater", return_value=updater)

    assert main._publish_sheet(mocker.Mock(), secrets, tmp_path) == 1

    assert events == ["truncate", "backup"]
    save_backup.assert_called_once()
    old_item.delete.assert_called_once_with()


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
