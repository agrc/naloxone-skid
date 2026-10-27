const PROJECT_ID = "ut-dts-ugrc-naloxone-skid-dev";
const REGION = "us-central1";
const JOB_NAME = "default";

const JOB_URL = `https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run`;

function onOpen() {
  const ui = SpreadsheetApp.getUi();
  ui.createMenu("Deployment")
    .addItem("Update AGOL feature layer.", "callUpdateFunction")
    .addToUi();
}

function escapeHtml(str) {
  /**
   * Helper function to escape HTML special characters to prevent UI modal errors.
   */
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function callUpdateFunction() {
  // Safe UI acquisition
  let ui;
  try {
    ui = SpreadsheetApp.getUi();
  } catch (e) {
    Logger.log(
      "UI context not available (running in background/trigger mode).",
    );
  }

  const token = ScriptApp.getOAuthToken();
  const options = {
    method: "post",
    contentType: "application/json",
    headers: { Authorization: "Bearer " + token },
    muteHttpExceptions: true,
  };

  let responseCode = 0;
  let responseText = "";
  let responseJson = null;
  try {
    const response = UrlFetchApp.fetch(JOB_URL, options);
    responseCode = response.getResponseCode();
    responseText = response.getContentText();

    try {
      responseJson = JSON.parse(responseText);
    } catch (e) {
      responseJson = null;
    }
  } catch (err) {
    responseText = err.toString();
  }

  // GCP returns HTTP 200/202 with an operation/metadata payload when a job is successfully queued
  const isGcpSuccess =
    (responseCode === 200 || responseCode === 202) &&
    responseJson &&
    (responseJson.name ||
      responseJson.metadata ||
      responseJson.done !== undefined);

  const isSuccess =
    isGcpSuccess || (responseJson && responseJson.success === true);
  const title = isSuccess ? "Deploy Successful" : "Deploy Unsuccessful";

  var htmlOutput = HtmlService.createHtmlOutput(
    '<div style="font-family: sans-serif; padding: 10px;">',
  );

  if (isSuccess) {
    const executionName =
      responseJson.metadata?.name ||
      responseJson.name ||
      "Job execution started.";

    htmlOutput.append(
      '<h4 style="color: #2e7d32;">Data successfully submitted to update AGOL Feature Layer.</h4>',
    );
    htmlOutput.append(
      `<p><strong>Execution:</strong> <code>${escapeHtml(executionName)}</code></p>`,
    );
  } else {
    const errorDetails = responseJson
      ? JSON.stringify(responseJson, null, 2)
      : responseText;
    htmlOutput.append(
      `<h4 style="color: #c62828;">Deployment Error (HTTP ${responseCode})</h4>`,
    );
    htmlOutput.append(
      `<pre style="white-space: pre-wrap; background: #f4f4f4; padding: 10px; border-radius: 4px; max-height: 300px; overflow-y: auto;">${escapeHtml(errorDetails)}</pre>`,
    );
  }

  // Parse validation error arrays if returned by a custom endpoint wrapper
  if (responseJson) {
    [
      "queryLayerValidationErrors",
      "relatedTableValidationErrors",
      "relationshipClassValidationErrors",
    ].forEach((key) => {
      if (Array.isArray(responseJson[key]) && responseJson[key].length > 0) {
        htmlOutput.append(`<h4>${escapeHtml(key)}</h4>`);
        responseJson[key].forEach((msg) => {
          htmlOutput.append(
            `<p style="color: #d32f2f;">${escapeHtml(msg)}</p>`,
          );
        });
      }
    });
  }

  htmlOutput.append("</div>");

  // Display dialog in Spreadsheet or log output if running headlessly
  if (ui) {
    ui.showModalDialog(htmlOutput.setWidth(550).setHeight(350), title);
  } else {
    Logger.log(`[${title}] HTTP ${responseCode}: ${responseText}`);
  }
}
