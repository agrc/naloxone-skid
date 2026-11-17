# naloxone-skid

[![Release Events](https://github.com/agrc/naloxone-skid/actions/workflows/release.yml/badge.svg)](https://github.com/agrc/naloxone-skid/actions/workflows/release.yml)
[![Push Events](https://github.com/agrc/naloxone-skid/actions/workflows/push.yml/badge.svg)](https://github.com/agrc/naloxone-skid/actions/workflows/push.yml)

This skid maintains an ArcGIS Online feature layer of organizations that provide naloxone-related services. It reads organization information from a Google Sheet, checks website URLs, geocodes addresses, and writes the website and geocoding results back to the sheet. AGOL publishing is a separate step so the sheet can be reviewed and geocoding rerun without replacing the feature layer.

This skid is executed on demand by VIPP employees via a via Google Apps Scripts deployment addon. VIPP team manages the source data in a Google Sheet in a shared drive.

## Running a sheet deployment

Use the Google Sheets deployment add-on after updating the source spreadsheet. The "Deployment" menu provides two actions:

1. **Check websites and geocode sheet** checks website URLs, geocodes addresses, and writes results to the sheet without changing AGOL. Run this again as needed while reviewing or correcting the sheet.
2. **Publish sheet data to AGOL** loads the saved coordinates and source data from the sheet into AGOL. It does not rerun URL checks or geocoding.

Publishing replaces the AGOL feature layer as before, so use the publish action only when the sheet is ready. Running `naloxone` directly with no arguments still runs preparation followed by publishing; `naloxone --mode prepare` and `naloxone --mode publish` run the steps independently.

An example script and json config can be found in the src/apps-script directory.

Note that the Apps Scripts GCP project needs to be pointed at the same GCP project as the hosting project.

To grant someone access to be able to use the deploy script, add them to the "gcp-dts-ugrc-deq-enviro-admins@utah.gov" google group.

The [DEQ Enviro config spreadsheet deploy add-on](https://github.com/agrc/deq-enviro#config-spreadsheet-deploy-addon) is a reference for this spreadsheet-driven deployment pattern.

## Runtime Environment
`naloxone-skid` runs as a Google Cloud Run Job and can also be run locally for development. GitHub Actions builds and deploys the container to the dev project when changes are pushed to `dev`, and deploys to production when a release is published. The container image is stored in Artifact Registry, and the job reads its secrets from Secret Manager.

The skid is started on demand from the Google Sheets deployment add-on; it does not use a Cloud Scheduler job. The GCP project must have the Cloud Run Job, Artifact Registry repository, Secret Manager secret (`skid-secrets`), and the service accounts and Workload Identity Federation permissions used by GitHub Actions and Cloud Run. The Apps Script GCP project must be the same project as the one hosting the skid.

The Google account running either Apps Script action must also be authorized to run the Cloud Run Job with execution overrides (`run.jobs.runWithOverrides`), which are used to select the preparation or publishing mode.

## Running Locally
Use Python 3.13, matching the version used to build the Cloud Run container. Clone the repository, then create the local secrets file. For local development, the `secrets.json` file holds all the login info, etc. A template is available in the repo's root directory. 

A separate `config.py` module holds non-secret configuration values. These are accessed by importing the module and accessing them directly.

```powershell
Copy-Item src\naloxone\secrets\secrets_template.json src\naloxone\secrets\secrets.json
```
From PowerShell at the repository root:

1. Create a Conda environment: `conda create --name naloxone-skid python=3.13 pip`
2. Activate it: `conda activate naloxone-skid`
3. Install the package and development dependencies: `python -m pip install -e ".[dev]"`
4. Authenticate to Google Sheets with application-default credentials: `gcloud auth application-default login  --scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/drive" `
5. Run the skid: `naloxone`

To run the tests, use `python -m pytest`.

## Attribution

This project was developed with the assistance of [GitHub Copilot](https://github.com/features/copilot).
