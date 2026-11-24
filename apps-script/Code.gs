const PROJECT_ID = "ut-dts-ugrc-naloxone-skid-dev";
const REGION = "us-central1";
const JOB_NAME = "default";

const JOB_URL = `https://run.googleapis.com/v2/projects/${PROJECT_ID}/locations/${REGION}/jobs/${JOB_NAME}:run`;
const LOGGING_URL = "https://logging.googleapis.com/v2/entries:list";

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu("Deployment")
    .addItem("Show sidebar", "showSidebar")
    .addToUi();
}

function showSidebar() {
  const widget = HtmlService.createHtmlOutputFromFile("Sidebar").setTitle(
    "Deployment Controls",
  );
  SpreadsheetApp.getUi().showSidebar(widget);
}

function callPrepareFunction() {
  return callUpdateFunction("prepare");
}

function callPublishFunction() {
  return callUpdateFunction("publish");
}

/**
 * 1. Triggers the Cloud Run Job and returns the Operation resource name for polling.
 */
function callUpdateFunction(mode) {
  mode = mode || "all";
  const action = mode === "prepare" ? "Sheet preparation" : "AGOL publishing";

  const token = ScriptApp.getOAuthToken();
  const options = {
    method: "post",
    contentType: "application/json",
    headers: { Authorization: "Bearer " + token },
    payload: JSON.stringify({
      overrides: {
        containerOverrides: [{ args: ["--mode", mode] }],
      },
    }),
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
    return {
      success: false,
      title: `${action} Failed`,
      message: "Network or script execution error.",
      details: err.toString(),
    };
  }

  const isGcpSuccess =
    (responseCode === 200 || responseCode === 202) &&
    responseJson &&
    (responseJson.name ||
      responseJson.metadata ||
      responseJson.done !== undefined);

  if (isGcpSuccess) {
    const operationName = responseJson.name;
    const executionName = responseJson.metadata?.name || operationName;

    return {
      success: true,
      action: action,
      operationName: operationName,
      executionName: executionName,
      title: `${action} Queued`,
      message: "Getting things ready...",
    };
  }

  // Handle immediate API rejection (e.g., 403 Permission Denied, 400 Bad Request)
  const errorDetails = responseJson
    ? JSON.stringify(responseJson, null, 2)
    : responseText;

  return {
    success: false,
    title: `${action} Failed (HTTP ${responseCode})`,
    message: "Something went wrong while starting the task.",
    details: errorDetails,
  };
}

/**
 * 2. Polls the Cloud Run Operation endpoint to check if execution has completed.
 */
function checkJobStatus(operationName, executionName, action) {
  const token = ScriptApp.getOAuthToken();
  const pollUrl = `https://run.googleapis.com/v2/${operationName}`;

  const options = {
    method: "get",
    headers: { Authorization: "Bearer " + token },
    muteHttpExceptions: true,
  };

  try {
    const response = UrlFetchApp.fetch(pollUrl, options);
    const code = response.getResponseCode();
    const text = response.getContentText();
    const data = JSON.parse(text);

    if (code !== 200) {
      return {
        done: true,
        success: false,
        title: `${action} Status Error (HTTP ${code})`,
        message: "Unable to check task progress.",
        details: text,
      };
    }

    // Still running
    if (!data.done) {
      const runningCount = data.metadata?.runningCount || 0;
      const statusMsg =
        runningCount > 0 ? "Processing your data..." : "Starting up...";

      return {
        done: false,
        message: statusMsg,
      };
    }

    // Execution finished: check if it succeeded or failed
    const execResource = data.response || data.metadata || {};
    const failedCount = execResource.failedCount || 0;
    const hasError = Boolean(data.error) || failedCount > 0;

    // Extract short execution ID (the last segment after /executions/)
    const execFullName = execResource.name || executionName || "";
    const shortExecId = execFullName.split("/").pop();

    if (!hasError) {
      return {
        done: true,
        success: true,
        title: `${action} Complete`,
        message: "Finished successfully.",
        details: `Execution: ${shortExecId}`,
      };
    }

    // Job failed: build summary and query Cloud Logging for container output
    const logs = [data.error?.message, fetchExecutionLogs(shortExecId)]
      .filter(Boolean)
      .join("\n\n");

    return {
      done: true,
      success: false,
      title: `${action} Failed`,
      message: "Something went wrong during the update.",
      details: logs,
    };
  } catch (err) {
    return {
      done: true,
      success: false,
      title: `${action} Status Error`,
      message: "Lost connection while checking task progress.",
      details: err.toString(),
    };
  }
}

/**
 * 3. Queries Cloud Logging for stderr/stdout entries from the failed execution.
 */
function fetchExecutionLogs(shortExecId) {
  if (!shortExecId) {
    return "No execution ID available to retrieve logs.";
  }

  const token = ScriptApp.getOAuthToken();

  // Filter logs specifically for this Cloud Run Job execution
  const filter = [
    `resource.type="cloud_run_job"`,
    `resource.labels.job_name="${JOB_NAME}"`,
    `resource.labels.location="${REGION}"`,
    `labels."run.googleapis.com/execution_name"="${shortExecId}"`,
  ].join(" AND ");

  const payload = {
    resourceNames: [`projects/${PROJECT_ID}`],
    filter: filter,
    orderBy: "timestamp desc",
    pageSize: 25,
  };

  const options = {
    method: "post",
    contentType: "application/json",
    headers: { Authorization: "Bearer " + token },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
  };

  try {
    const response = UrlFetchApp.fetch(LOGGING_URL, options);
    const code = response.getResponseCode();
    const json = JSON.parse(response.getContentText());

    if (code !== 200) {
      return `Could not fetch logs (HTTP ${code}): ${JSON.stringify(json.error || json)}`;
    }

    const entries = json.entries || [];
    if (entries.length === 0) {
      return `Execution ID: ${shortExecId}\nNo error log entries were found yet.`;
    }

    // Reverse to chronological order and format readable log lines
    const formattedLines = entries.reverse().map((entry) => {
      const time = entry.timestamp
        ? entry.timestamp.split("T")[1].replace("Z", "")
        : "";
      const severity = entry.severity || "INFO";
      const msg =
        entry.textPayload ||
        (entry.jsonPayload ? JSON.stringify(entry.jsonPayload) : "") ||
        (entry.protoPayload ? entry.protoPayload.status?.message : "") ||
        "[No text payload]";
      return `[${time}] [${severity}] ${msg}`;
    });

    return `Execution: ${shortExecId}\n--- Error Logs (Last ${entries.length} entries) ---\n${formattedLines.join("\n")}`;
  } catch (err) {
    return `Execution: ${shortExecId}\nError retrieving logs: ${err.toString()}`;
  }
}
