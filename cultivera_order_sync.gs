const CULTIVERA_SPREADSHEET_ID = '1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw';
const CULTIVERA_DATA_SHEET_NAME = 'Cultivera Data';
const CULTIVERA_SYNC_LOG_SHEET_NAME = 'Cultivera Sync Log';
const CULTIVERA_CONFIG_SHEET_NAME = 'Cultivera Sync Config';
const CULTIVERA_AUTH_URL = 'https://api-wa.cultiverapro.com/api/v1/auth/sign-in';
const CULTIVERA_EXPORT_URL = 'https://api-wa.cultiverapro.com/api/v1/Orders/export-order-details-to-excel';
const CULTIVERA_TRANSACTION_STATUS_URL_PREFIX = 'https://api-wa.cultiverapro.com/api/v1/transactions/status/';
const CULTIVERA_TRANSACTION_POLL_ATTEMPTS = 12;
const CULTIVERA_TRANSACTION_POLL_SLEEP_MS = 5000;
const CULTIVERA_SIGN_IN_RETRY_SLEEP_MS = 5000;
const CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL = 'Include Cancelled Orders';
const CULTIVERA_CANCELLED_ORDER_STATUSES = ['Cancelled'];

const CULTIVERA_PROP_TOKEN = 'CULTIVERA_BEARER_TOKEN';
const CULTIVERA_PROP_USERNAME = 'CULTIVERA_USERNAME';
const CULTIVERA_PROP_PASSWORD = 'CULTIVERA_PASSWORD';
const CULTIVERA_PROP_PAYLOAD = 'CULTIVERA_EXPORT_PAYLOAD_JSON';
const CULTIVERA_PROP_TZO = 'CULTIVERA_TZO_MINUTES';

function checkCultiveraSyncSetup() {
  const props = PropertiesService.getScriptProperties();
  const credentials = getCultiveraCredentials_();
  const spreadsheet = SpreadsheetApp.openById(CULTIVERA_SPREADSHEET_ID);
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_DATA_SHEET_NAME);
  const includeCancelled = getCultiveraIncludeCancelledOrders_();
  Logger.log(`Spreadsheet: ${spreadsheet.getName()}`);
  Logger.log(`Target sheet: ${sheet.getName()}; rows: ${sheet.getLastRow()}; columns: ${sheet.getLastColumn()}`);
  Logger.log(`${CULTIVERA_CONFIG_SHEET_NAME}: ${CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL} = ${includeCancelled}`);
  Logger.log(`${CULTIVERA_PROP_TOKEN} configured: ${Boolean(props.getProperty(CULTIVERA_PROP_TOKEN))}`);
  Logger.log(`${CULTIVERA_PROP_USERNAME} configured: ${Boolean(credentials.username)}; length: ${credentials.username.length}`);
  Logger.log(`${CULTIVERA_PROP_PASSWORD} configured: ${Boolean(credentials.password)}; length: ${credentials.password.length}`);
  Logger.log(`${CULTIVERA_PROP_PAYLOAD} configured: ${Boolean(props.getProperty(CULTIVERA_PROP_PAYLOAD))}`);
  Logger.log(`${CULTIVERA_PROP_TZO}: ${props.getProperty(CULTIVERA_PROP_TZO) || '-420 default'}`);
}

function setupCultiveraSyncConfig() {
  const sheet = getOrCreateCultiveraConfigSheet_();
  Logger.log(
    `${CULTIVERA_CONFIG_SHEET_NAME} ready. ` +
    `${CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL}: ${sheet.getRange('B2').getValue()}`
  );
}

function testCultiveraSignIn() {
  const token = refreshCultiveraBearerToken_();
  Logger.log(`Cultivera sign-in OK. Token stored: ${Boolean(token)}; token length: ${token.length}.`);
}

function debugCultiveraSignInResponse() {
  const credentials = getCultiveraCredentials_();
  const username = credentials.username;
  const password = credentials.password;
  if (!username || !password) {
    throw new Error(`Missing Script Properties: ${CULTIVERA_PROP_USERNAME} and/or ${CULTIVERA_PROP_PASSWORD}`);
  }

  const response = fetchCultiveraSignIn_(username, password);

  Logger.log(`Sign-in HTTP status: ${response.getResponseCode()}`);
  Logger.log(`Sign-in Content-Type: ${headerValue_(response.getAllHeaders(), 'Content-Type') || headerValue_(response.getAllHeaders(), 'content-type') || ''}`);
  Logger.log(`Sign-in header keys: ${Object.keys(response.getAllHeaders()).join(', ')}`);
  Logger.log(`Sign-in token candidates: ${tokenCandidateSummary_(response)}`);
  Logger.log(`Sign-in redacted preview: ${redactedTextPreview_(response, 800)}`);
}

function testCultiveraExportRequest() {
  const response = fetchCultiveraExport_();
  const headers = response.getAllHeaders();
  Logger.log(`HTTP status: ${response.getResponseCode()}`);
  Logger.log(`Content-Type: ${headerValue_(headers, 'Content-Type') || headerValue_(headers, 'content-type') || ''}`);
  Logger.log(`Content-Disposition: ${headerValue_(headers, 'Content-Disposition') || headerValue_(headers, 'content-disposition') || ''}`);
  Logger.log(`Body bytes: ${response.getBlob().getBytes().length}`);
  Logger.log(`Body preview: ${safeTextPreview_(response, 300)}`);
  const transactionId = transactionIdFromResponse_(response);
  if (transactionId) {
    Logger.log(`Cultivera created async export transaction: ${transactionId}`);
    const statusResponse = pollCultiveraTransaction_(transactionId);
    logCultiveraResponse_('Final transaction response', statusResponse);
  }
}

function syncCultiveraOrdersToSheet() {
  const startedAt = new Date();
  try {
    const values = fetchCultiveraExportValues_();
    if (!values.length || !values[0].length) {
      throw new Error('Cultivera response did not contain tabular data.');
    }

    writeValuesToCultiveraData_(values);
    appendCultiveraSyncLog_('OK', values.length - 1, values[0].length, `Started ${startedAt.toISOString()}`);
    Logger.log(`Synced ${Math.max(0, values.length - 1)} rows and ${values[0].length} columns.`);
  } catch (err) {
    appendCultiveraSyncLog_('ERROR', 0, 0, err.message || String(err));
    throw err;
  }
}

function createCultiveraOrderSyncTrigger() {
  deleteCultiveraOrderSyncTriggers();
  ScriptApp.newTrigger('syncCultiveraOrdersToSheet')
    .timeBased()
    .everyHours(1)
    .create();
}

function deleteCultiveraOrderSyncTriggers() {
  ScriptApp.getProjectTriggers().forEach(trigger => {
    if (trigger.getHandlerFunction() === 'syncCultiveraOrdersToSheet') {
      ScriptApp.deleteTrigger(trigger);
    }
  });
}

function fetchCultiveraExportValues_() {
  const standardPayload = getCultiveraExportPayload_();
  const standardValues = responseToSheetValues_(
    fetchCultiveraExport_(standardPayload, 'Standard Cultivera export')
  );

  if (!getCultiveraIncludeCancelledOrders_()) {
    return standardValues;
  }

  const cancelledPayload = getCultiveraExportPayload_();
  const payloadChanges = applyCultiveraCancelledOrderSetting_(cancelledPayload, true);
  Logger.log(
    `${CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL} enabled. ` +
    `Cancelled export payload adjustments: ${payloadChanges.length ? payloadChanges.join('; ') : 'none detected'}`
  );

  if (!payloadChanges.length) {
    Logger.log('Cancelled export skipped because the stored payload did not expose a cancelled-order filter.');
    return standardValues;
  }

  const cancelledValues = responseToSheetValues_(
    fetchCultiveraExport_(cancelledPayload, 'Cancelled Cultivera export')
  );
  return combineCultiveraSheetValues_([standardValues, cancelledValues]);
}

function getCultiveraIncludeCancelledOrders_() {
  const sheet = getOrCreateCultiveraConfigSheet_();
  const value = sheet.getRange('B2').getValue();
  return value === true || ['true', 'yes', 'y', '1'].includes(String(value).trim().toLowerCase());
}

function getOrCreateCultiveraConfigSheet_() {
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_CONFIG_SHEET_NAME);
  const currentLabel = String(sheet.getRange('A2').getValue() || '').trim();
  if (sheet.getRange('A1').getValue() !== 'Setting') {
    sheet.getRange('A1:B1').setValues([['Setting', 'Value']]);
    sheet.setFrozenRows(1);
  }
  if (currentLabel !== CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL) {
    sheet.getRange('A2').setValue(CULTIVERA_INCLUDE_CANCELLED_SETTING_LABEL);
  }
  if (sheet.getRange('B2').isBlank()) {
    sheet.getRange('B2').setValue(false);
  }
  sheet.getRange('B2').insertCheckboxes();
  sheet.getRange('B2').setNote(
    'When checked, sync attempts to add Cancelled orders to the Cultivera export payload.'
  );
  sheet.autoResizeColumns(1, 2);
  return sheet;
}

function fetchCultiveraExport_(payload, label) {
  const exportLabel = label || 'Cultivera export';
  let response = fetchCultiveraExportWithToken_(getCultiveraBearerToken_(), payload);
  if (response.getResponseCode() === 401 && canRefreshCultiveraToken_()) {
    Logger.log(`${exportLabel} returned HTTP 401. Refreshing bearer token and retrying once.`);
    response = fetchCultiveraExportWithToken_(refreshCultiveraBearerToken_(), payload);
  }

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`${exportLabel} failed with HTTP ${status}: ${redactedTextPreview_(response, 500)}`);
  }
  return response;
}

function fetchCultiveraExportWithToken_(token, payload) {
  const exportPayload = payload || getCultiveraExportPayload_();
  return UrlFetchApp.fetch(CULTIVERA_EXPORT_URL, {
    method: 'post',
    contentType: 'application/json;charset=UTF-8',
    payload: JSON.stringify(exportPayload),
    headers: cultiveraHeaders_(token),
    muteHttpExceptions: true
  });
}

function applyCultiveraCancelledOrderSetting_(payload, includeCancelled) {
  if (!includeCancelled || payload === null || typeof payload !== 'object') {
    return [];
  }
  const changes = [];
  patchCultiveraCancelledOrderValue_(payload, 'payload', false, changes);
  return changes;
}

function patchCultiveraCancelledOrderValue_(value, path, insideStatusFilter, changes) {
  if (value === null || value === undefined) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => {
      patchCultiveraCancelledOrderValue_(item, `${path}[${i}]`, insideStatusFilter, changes);
    });
    return;
  }
  if (typeof value !== 'object') {
    return;
  }

  Object.keys(value).forEach(key => {
    const child = value[key];
    const childPath = `${path}.${key}`;
    const keyLooksCancelled = /cancell?ed|cancelled|canceled/i.test(key);
    const keyLooksStatus = /status/i.test(key);
    const childIsStatusFilter = insideStatusFilter || keyLooksStatus;

    if (keyLooksCancelled && typeof child === 'boolean') {
      if (/exclude|hide|omit|without/i.test(key)) {
        if (child !== false) {
          value[key] = false;
          changes.push(`${childPath}=false`);
        }
      } else if (/include|show|with|allow/i.test(key) || insideStatusFilter || keyLooksStatus) {
        if (child !== true) {
          value[key] = true;
          changes.push(`${childPath}=true`);
        }
      }
    }

    if (keyLooksStatus && Array.isArray(child)) {
      const added = addCancelledStatusesToArray_(child);
      if (added.length) {
        changes.push(`${childPath} += ${added.join(', ')}`);
      }
    }

    patchCultiveraCancelledOrderValue_(child, childPath, childIsStatusFilter, changes);
  });
}

function addCancelledStatusesToArray_(values) {
  if (!values.length || !values.every(value => typeof value === 'string')) {
    return [];
  }
  const lowerValues = values.map(value => value.trim().toLowerCase());
  const added = [];
  CULTIVERA_CANCELLED_ORDER_STATUSES.forEach(status => {
    if (!lowerValues.includes(status.toLowerCase())) {
      values.push(status);
      added.push(status);
    }
  });
  return added;
}

function combineCultiveraSheetValues_(valueSets) {
  const tables = (valueSets || []).filter(values => values && values.length && values[0] && values[0].length);
  if (!tables.length) {
    return [];
  }
  if (tables.length === 1) {
    return dedupeCultiveraSheetValues_(tables[0]);
  }

  const combinedHeaders = [];
  tables.forEach(values => {
    values[0].forEach(header => {
      const headerName = String(header || '').trim();
      if (headerName && !combinedHeaders.includes(headerName)) {
        combinedHeaders.push(headerName);
      }
    });
  });

  const combinedRows = [];
  tables.forEach(values => {
    const sourceHeaders = values[0].map(header => String(header || '').trim());
    values.slice(1).forEach(row => {
      const rowByHeader = {};
      sourceHeaders.forEach((header, i) => {
        if (header) {
          rowByHeader[header] = row[i];
        }
      });
      combinedRows.push(combinedHeaders.map(header => {
        const value = rowByHeader[header];
        return value === undefined ? '' : value;
      }));
    });
  });

  const deduped = dedupeCultiveraSheetValues_([combinedHeaders, ...combinedRows]);
  Logger.log(
    `Combined Cultivera exports: ${tables.map(values => Math.max(0, values.length - 1)).join(' + ')} rows ` +
    `=> ${Math.max(0, deduped.length - 1)} rows after exact duplicate removal.`
  );
  return deduped;
}

function dedupeCultiveraSheetValues_(values) {
  if (!values || values.length <= 2) {
    return values || [];
  }
  const headers = values[0];
  const seen = {};
  const rows = [];
  values.slice(1).forEach(row => {
    const key = row.map(normalizeCultiveraSheetValueForDedupe_).join('\u001f');
    if (!seen[key]) {
      seen[key] = true;
      rows.push(row);
    }
  });
  if (rows.length !== values.length - 1) {
    Logger.log(`Removed ${values.length - 1 - rows.length} exact duplicate Cultivera rows.`);
  }
  return [headers, ...rows];
}

function normalizeCultiveraSheetValueForDedupe_(value) {
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (value === null || value === undefined) {
    return '';
  }
  return String(value).trim();
}

function getCultiveraBearerToken_() {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty(CULTIVERA_PROP_TOKEN);
  if (token) {
    return normalizeBearerToken_(token);
  }

  if (canRefreshCultiveraToken_()) {
    return refreshCultiveraBearerToken_();
  }

  if (!token) {
    throw new Error(
      `Missing Script Property: ${CULTIVERA_PROP_TOKEN}. ` +
      `For automatic refresh, set ${CULTIVERA_PROP_USERNAME} and ${CULTIVERA_PROP_PASSWORD}.`
    );
  }
  return normalizeBearerToken_(token);
}

function canRefreshCultiveraToken_() {
  const credentials = getCultiveraCredentials_();
  return Boolean(credentials.username && credentials.password);
}

function refreshCultiveraBearerToken_() {
  const props = PropertiesService.getScriptProperties();
  const credentials = getCultiveraCredentials_();
  const username = credentials.username;
  const password = credentials.password;
  if (!username || !password) {
    throw new Error(`Missing Script Properties: ${CULTIVERA_PROP_USERNAME} and/or ${CULTIVERA_PROP_PASSWORD}`);
  }

  const response = fetchCultiveraSignIn_(username, password);

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`Cultivera sign-in failed with HTTP ${status}: ${redactedTextPreview_(response, 500)}`);
  }

  const token = tokenFromCultiveraAuthResponse_(response);
  if (!token) {
    throw new Error(
      `Cultivera sign-in succeeded, but no bearer token was found. ` +
      `Response preview: ${redactedTextPreview_(response, 500)}`
    );
  }

  props.setProperty(CULTIVERA_PROP_TOKEN, token);
  return token;
}

function getCultiveraCredentials_() {
  const props = PropertiesService.getScriptProperties();
  return {
    username: cleanCultiveraCredential_(props.getProperty(CULTIVERA_PROP_USERNAME)),
    password: cleanCultiveraCredential_(props.getProperty(CULTIVERA_PROP_PASSWORD))
  };
}

function cleanCultiveraCredential_(value) {
  let cleaned = String(value || '').trim();
  if (
    (cleaned.startsWith('"') && cleaned.endsWith('"')) ||
    (cleaned.startsWith("'") && cleaned.endsWith("'"))
  ) {
    cleaned = cleaned.slice(1, -1).trim();
  }
  return cleaned;
}

function fetchCultiveraSignIn_(username, password) {
  let response = fetchCultiveraSignInOnce_(username, password);
  if (response.getResponseCode() >= 500) {
    Logger.log(`Cultivera sign-in returned HTTP ${response.getResponseCode()}. Waiting and retrying once.`);
    Utilities.sleep(CULTIVERA_SIGN_IN_RETRY_SLEEP_MS);
    response = fetchCultiveraSignInOnce_(username, password);
  }
  return response;
}

function fetchCultiveraSignInOnce_(username, password) {
  return UrlFetchApp.fetch(CULTIVERA_AUTH_URL, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      username: username,
      password: password,
      utcOffset: cultiveraTimezoneOffset_()
    }),
    headers: cultiveraSignInHeaders_(),
    muteHttpExceptions: true
  });
}

function cultiveraSignInHeaders_() {
  return {
    Accept: 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    Origin: 'https://wa.cultiverapro.com',
    Referer: 'https://wa.cultiverapro.com/',
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    'x-rts': Math.floor(Date.now() / 1000).toString(),
    'x-tzo': String(cultiveraTimezoneOffset_())
  };
}

function cultiveraTimezoneOffset_() {
  const raw = PropertiesService.getScriptProperties().getProperty(CULTIVERA_PROP_TZO);
  const parsed = Number(raw || -420);
  return Number.isFinite(parsed) ? parsed : -420;
}

function tokenFromCultiveraAuthResponse_(response) {
  const text = response.getContentText().trim();
  if (/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(text)) {
    return text;
  }

  try {
    const json = JSON.parse(text);
    const jsonToken = authTokenFromJson_(json);
    if (jsonToken) {
      return normalizeBearerToken_(jsonToken);
    }
  } catch (err) {
    // Fall through to header parsing.
  }

  const headerToken = tokenFromCultiveraAuthHeaders_(response.getAllHeaders());
  if (headerToken) {
    return headerToken;
  }
  return '';
}

function tokenFromCultiveraAuthHeaders_(headers) {
  const candidates = [
    'Authorization', 'authorization',
    'X-Auth-Token', 'x-auth-token',
    'X-Access-Token', 'x-access-token'
  ];
  for (const name of candidates) {
    const token = normalizeBearerToken_(headerValue_(headers, name));
    if (token) {
      return token;
    }
  }
  return '';
}

function authTokenFromJson_(json) {
  const candidates = [];
  collectAuthTokenCandidates_(json, '', candidates);
  if (!candidates.length) {
    return '';
  }

  candidates.sort((a, b) => {
    if (a.isJwt !== b.isJwt) {
      return a.isJwt ? -1 : 1;
    }
    if (a.preferred !== b.preferred) {
      return a.preferred ? -1 : 1;
    }
    return b.value.length - a.value.length;
  });
  return candidates[0].value;
}

function collectAuthTokenCandidates_(value, path, candidates) {
  if (value === null || value === undefined) {
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, i) => collectAuthTokenCandidates_(item, `${path}[${i}]`, candidates));
    return;
  }
  if (typeof value !== 'object') {
    return;
  }

  const preferredKeys = {
    access_token: true,
    accessToken: true,
    AccessToken: true,
    bearer_token: true,
    bearerToken: true,
    BearerToken: true,
    jwt: true,
    Jwt: true,
    id_token: true,
    idToken: true
  };

  for (const key of Object.keys(value)) {
    const child = value[key];
    const childPath = path ? `${path}.${key}` : key;
    if (
      typeof child === 'string' &&
      child.trim() &&
      /token|bearer|jwt/i.test(key) &&
      !/refresh/i.test(key)
    ) {
      const token = normalizeBearerToken_(child);
      candidates.push({
        path: childPath,
        value: token,
        isJwt: isJwtToken_(token),
        preferred: Boolean(preferredKeys[key])
      });
    }
    collectAuthTokenCandidates_(child, childPath, candidates);
  }
}

function normalizeBearerToken_(token) {
  return String(token || '').replace(/^Bearer\s+/i, '').trim();
}

function isJwtToken_(token) {
  return /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(String(token || '').trim());
}

function cultiveraHeaders_(token) {
  const props = PropertiesService.getScriptProperties();
  return {
    Authorization: `Bearer ${normalizeBearerToken_(token)}`,
    Accept: 'application/json, text/plain, */*',
    Origin: 'https://wa.cultiverapro.com',
    Referer: 'https://wa.cultiverapro.com/',
    'x-rts': Math.floor(Date.now() / 1000).toString(),
    'x-tzo': props.getProperty(CULTIVERA_PROP_TZO) || '-420'
  };
}

function getCultiveraExportPayload_() {
  const raw = PropertiesService.getScriptProperties().getProperty(CULTIVERA_PROP_PAYLOAD);
  if (!raw) {
    throw new Error(`Missing Script Property: ${CULTIVERA_PROP_PAYLOAD}. Paste the DevTools Payload JSON there.`);
  }
  try {
    return JSON.parse(raw);
  } catch (err) {
    throw new Error(`${CULTIVERA_PROP_PAYLOAD} is not valid JSON: ${err.message || err}`);
  }
}

function responseToSheetValues_(response) {
  const headers = response.getAllHeaders();
  const contentType = String(
    headerValue_(headers, 'Content-Type') ||
    headerValue_(headers, 'content-type') ||
    ''
  ).toLowerCase();
  const disposition = String(
    headerValue_(headers, 'Content-Disposition') ||
    headerValue_(headers, 'content-disposition') ||
    ''
  ).toLowerCase();

  if (contentType.includes('json')) {
    const json = JSON.parse(response.getContentText());
    const transactionId = transactionIdFromJson_(json);
    if (transactionId && isTransactionStartJson_(json)) {
      return responseToSheetValues_(pollCultiveraTransaction_(transactionId));
    }
    const downloadUrl = downloadUrlFromJson_(json);
    if (downloadUrl) {
      return responseToSheetValues_(fetchCultiveraDownloadUrl_(downloadUrl));
    }
    return jsonToSheetValues_(json);
  }
  if (contentType.includes('csv') || disposition.includes('.csv')) {
    return Utilities.parseCsv(response.getContentText());
  }
  return excelBlobToSheetValues_(response.getBlob());
}

function transactionIdFromResponse_(response) {
  try {
    return transactionIdFromJson_(JSON.parse(response.getContentText()));
  } catch (err) {
    return '';
  }
}

function transactionIdFromJson_(json) {
  return String((json && (json.TransactionId || json.transactionId || json.transactionID)) || '').trim();
}

function isTransactionStartJson_(json) {
  if (!transactionIdFromJson_(json)) {
    return false;
  }
  const keys = Object.keys(json || {});
  return keys.length === 1 || !keys.some(key => /status|state|complete|done|result|download|file|url|uri/i.test(key));
}

function fetchCultiveraTransactionStatus_(transactionId) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty(CULTIVERA_PROP_TOKEN);
  if (!token) {
    throw new Error(`Missing Script Property: ${CULTIVERA_PROP_TOKEN}`);
  }

  const response = UrlFetchApp.fetch(
    CULTIVERA_TRANSACTION_STATUS_URL_PREFIX + encodeURIComponent(transactionId),
    {
      method: 'get',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/json, text/plain, */*',
        Origin: 'https://wa.cultiverapro.com',
        Referer: 'https://wa.cultiverapro.com/',
        'x-rts': Math.floor(Date.now() / 1000).toString(),
        'x-tzo': props.getProperty(CULTIVERA_PROP_TZO) || '-420'
      },
      muteHttpExceptions: true
    }
  );

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`Cultivera transaction status failed with HTTP ${status}: ${safeTextPreview_(response, 500)}`);
  }
  return response;
}

function pollCultiveraTransaction_(transactionId) {
  let lastResponse = null;
  for (let attempt = 1; attempt <= CULTIVERA_TRANSACTION_POLL_ATTEMPTS; attempt++) {
    lastResponse = fetchCultiveraTransactionStatus_(transactionId);
    const info = transactionStatusInfo_(lastResponse);
    Logger.log(
      `Transaction ${transactionId} attempt ${attempt}/${CULTIVERA_TRANSACTION_POLL_ATTEMPTS}: ` +
      `${info.status || 'unknown'} · ${safeTextPreview_(lastResponse, 300)}`
    );

    if (info.failed) {
      throw new Error(`Cultivera export transaction ${transactionId} failed: ${safeTextPreview_(lastResponse, 500)}`);
    }
    if (info.downloadUrl) {
      return fetchCultiveraDownloadUrl_(info.downloadUrl);
    }
    if (info.done) {
      return lastResponse;
    }
    Utilities.sleep(CULTIVERA_TRANSACTION_POLL_SLEEP_MS);
  }

  throw new Error(
    `Cultivera export transaction ${transactionId} did not complete after ` +
    `${CULTIVERA_TRANSACTION_POLL_ATTEMPTS} attempts. Last response: ${safeTextPreview_(lastResponse, 500)}`
  );
}

function transactionStatusInfo_(response) {
  const info = {
    status: '',
    done: false,
    failed: false,
    downloadUrl: ''
  };
  const contentType = String(
    headerValue_(response.getAllHeaders(), 'Content-Type') ||
    headerValue_(response.getAllHeaders(), 'content-type') ||
    ''
  ).toLowerCase();

  if (!contentType.includes('json')) {
    info.done = true;
    return info;
  }

  try {
    const json = JSON.parse(response.getContentText());
    info.status = String(
      json.Status || json.status ||
      json.State || json.state ||
      json.TransactionStatus || json.transactionStatus ||
      ''
    );
    const lowerStatus = info.status.toLowerCase();
    const update = String(json.Update || json.update || '').toLowerCase();
    const pct = Number(json.Pct || json.pct || 0);
    info.failed = (
      Boolean(json.Failed || json.failed || json.HasError || json.hasError) ||
      /fail|error|cancel/.test(lowerStatus)
    );
    info.done = (
      Boolean(json.IsComplete || json.isComplete || json.Completed || json.completed || json.Done || json.done) ||
      Number(json.Status || json.status) === 2 ||
      pct >= 1 ||
      /complete|completed|success|succeeded|done|finished/.test(lowerStatus) ||
      /complete|completed|success|succeeded|done|finished/.test(update)
    );
    info.downloadUrl = downloadUrlFromJson_(json);
  } catch (err) {
    info.done = false;
  }
  return info;
}

function downloadUrlFromJson_(json) {
  const url = firstStringByKeyPattern_(json, /finalvalue|download|file|url|uri|href/i);
  if (!url) {
    return '';
  }
  if (/^https?:\/\//i.test(url)) {
    return url;
  }
  if (url.startsWith('/')) {
    return 'https://api-wa.cultiverapro.com' + url;
  }
  return '';
}

function firstStringByKeyPattern_(value, pattern) {
  if (value === null || value === undefined) {
    return '';
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstStringByKeyPattern_(item, pattern);
      if (found) {
        return found;
      }
    }
    return '';
  }
  if (typeof value !== 'object') {
    return '';
  }
  for (const key of Object.keys(value)) {
    const child = value[key];
    if (pattern.test(key) && typeof child === 'string' && child.trim()) {
      return child.trim();
    }
    const found = firstStringByKeyPattern_(child, pattern);
    if (found) {
      return found;
    }
  }
  return '';
}

function fetchCultiveraDownloadUrl_(url) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty(CULTIVERA_PROP_TOKEN);
  const response = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json, text/plain, */*',
      Origin: 'https://wa.cultiverapro.com',
      Referer: 'https://wa.cultiverapro.com/',
      'x-rts': Math.floor(Date.now() / 1000).toString(),
      'x-tzo': props.getProperty(CULTIVERA_PROP_TZO) || '-420'
    },
    muteHttpExceptions: true
  });
  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`Cultivera download failed with HTTP ${status}: ${safeTextPreview_(response, 500)}`);
  }
  return response;
}

function logCultiveraResponse_(label, response) {
  const headers = response.getAllHeaders();
  Logger.log(`${label} HTTP status: ${response.getResponseCode()}`);
  Logger.log(`${label} Content-Type: ${headerValue_(headers, 'Content-Type') || headerValue_(headers, 'content-type') || ''}`);
  Logger.log(`${label} Content-Disposition: ${headerValue_(headers, 'Content-Disposition') || headerValue_(headers, 'content-disposition') || ''}`);
  Logger.log(`${label} Body bytes: ${response.getBlob().getBytes().length}`);
  Logger.log(`${label} Body preview: ${safeTextPreview_(response, 500)}`);
}

function excelBlobToSheetValues_(blob) {
  if (typeof Drive === 'undefined' || !Drive.Files) {
    throw new Error('Enable Advanced Google Services > Drive API before importing Excel exports.');
  }

  const tempName = `cultivera-order-export-${new Date().toISOString()}`;
  blob.setName(`${tempName}.xlsx`);

  let convertedFile;
  if (Drive.Files.create) {
    convertedFile = Drive.Files.create(
      { name: tempName, mimeType: MimeType.GOOGLE_SHEETS },
      blob,
      { fields: 'id' }
    );
  } else if (Drive.Files.insert) {
    convertedFile = Drive.Files.insert(
      { title: tempName, mimeType: MimeType.GOOGLE_SHEETS },
      blob,
      { convert: true }
    );
  } else {
    throw new Error('Drive advanced service is enabled, but Files.create/insert is unavailable.');
  }

  try {
    const tempSpreadsheet = SpreadsheetApp.openById(convertedFile.id);
    const sourceSheet = tempSpreadsheet.getSheets()[0];
    return sourceSheet.getDataRange().getValues();
  } finally {
    trashDriveFile_(convertedFile.id);
  }
}

function jsonToSheetValues_(json) {
  const rows = firstArrayInCultiveraJson_(json);
  if (!rows.length) {
    return [['Response'], [JSON.stringify(json)]];
  }

  const headers = [];
  rows.forEach(row => {
    Object.keys(row || {}).forEach(key => {
      if (!headers.includes(key)) {
        headers.push(key);
      }
    });
  });

  return [
    headers,
    ...rows.map(row => headers.map(header => {
      const value = row ? row[header] : '';
      if (value === null || value === undefined) {
        return '';
      }
      return typeof value === 'object' ? JSON.stringify(value) : value;
    }))
  ];
}

function firstArrayInCultiveraJson_(json) {
  if (Array.isArray(json)) {
    return json;
  }
  const candidateKeys = ['data', 'items', 'orders', 'results', 'rows', 'value'];
  for (const key of candidateKeys) {
    if (Array.isArray(json && json[key])) {
      return json[key];
    }
  }
  return [];
}

function writeValuesToCultiveraData_(values) {
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_DATA_SHEET_NAME);
  sheet.clearContents();
  sheet.getRange(1, 1, values.length, values[0].length).setValues(values);
  sheet.setFrozenRows(1);
}

function getOrCreateCultiveraSheet_(name) {
  const spreadsheet = SpreadsheetApp.openById(CULTIVERA_SPREADSHEET_ID);
  return spreadsheet.getSheetByName(name) || spreadsheet.insertSheet(name);
}

function appendCultiveraSyncLog_(status, rows, columns, message) {
  const sheet = getOrCreateCultiveraSheet_(CULTIVERA_SYNC_LOG_SHEET_NAME);
  if (sheet.getLastRow() === 0) {
    sheet.appendRow(['Timestamp', 'Status', 'Rows', 'Columns', 'Message']);
  }
  sheet.appendRow([new Date(), status, rows, columns, message]);
}

function headerValue_(headers, name) {
  if (!headers) {
    return '';
  }
  return headers[name] || headers[String(name).toLowerCase()] || '';
}

function safeTextPreview_(response, limit) {
  try {
    return response.getContentText().slice(0, limit);
  } catch (err) {
    return '[binary response]';
  }
}

function redactedTextPreview_(response, limit) {
  return safeTextPreview_(response, limit)
    .replace(/Bearer\s+[A-Za-z0-9._-]+/gi, 'Bearer REDACTED')
    .replace(/([A-Za-z0-9_-]+\.){2}[A-Za-z0-9_-]+/g, 'JWT_REDACTED')
    .replace(/("(?:access_token|accessToken|AccessToken|bearer_token|bearerToken|BearerToken|token|Token|jwt|Jwt|id_token|idToken|refresh_token|refreshToken|RefreshToken)"\s*:\s*")[^"]+(")/g, '$1REDACTED$2');
}

function tokenCandidateSummary_(response) {
  const summaries = [];
  const headerToken = tokenFromCultiveraAuthHeaders_(response.getAllHeaders());
  if (headerToken) {
    summaries.push(`header:${isJwtToken_(headerToken) ? 'jwt' : 'non-jwt'}:${headerToken.length}`);
  }

  try {
    const text = response.getContentText().trim();
    if (isJwtToken_(text)) {
      summaries.push(`body:raw-jwt:${text.length}`);
    } else {
      const json = JSON.parse(text);
      const candidates = [];
      collectAuthTokenCandidates_(json, '', candidates);
      candidates.forEach(candidate => {
        summaries.push(
          `${candidate.path}:${candidate.isJwt ? 'jwt' : 'non-jwt'}:${candidate.value.length}`
        );
      });
    }
  } catch (err) {
    summaries.push('body:not-json-or-empty');
  }

  return summaries.join('; ') || 'none';
}

function trashDriveFile_(fileId) {
  try {
    DriveApp.getFileById(fileId).setTrashed(true);
  } catch (err) {
    Logger.log(`Could not trash temporary file ${fileId}: ${err.message || err}`);
  }
}
