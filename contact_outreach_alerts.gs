const CONTACT_LOG_SHEET_NAME = 'Contact Log';
const ALERT_CC = 'geoff@ksavagesupply.com,roger@ksavagesupply.com';

function createWeeklyOutreachDigestTrigger() {
  ScriptApp.newTrigger('sendWeeklyOutreachDigests')
    .timeBased()
    .onWeekDay(ScriptApp.WeekDay.MONDAY)
    .atHour(8)
    .create();
}

function sendWeeklyOutreachDigests() {
  const sheet = SpreadsheetApp.getActive().getSheetByName(CONTACT_LOG_SHEET_NAME);
  if (!sheet) {
    throw new Error(`Sheet not found: ${CONTACT_LOG_SHEET_NAME}`);
  }

  const values = sheet.getDataRange().getValues();
  if (values.length < 2) {
    return;
  }

  const headers = values[0].map(String);
  const sentWeekCol = ensureColumn_(sheet, headers, 'Alert Sent Week');
  const col = indexMap_(sheet.getRange(1, 1, 1, sheet.getLastColumn()).getValues()[0]);

  const weekStart = startOfWeek_(new Date());
  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekStart.getDate() + 6);
  weekEnd.setHours(23, 59, 59, 999);
  const weekKey = dateKey_(weekStart);

  const dueByRecipient = {};
  const rowNumbersByRecipient = {};

  for (let i = 1; i < values.length; i++) {
    const row = values[i];
    const recipient = value_(row, col, 'Alert Recipient');
    const nextDate = parseDate_(value_(row, col, 'Next Outreach Date'));
    const sentWeek = value_(row, col, 'Alert Sent Week');

    if (!recipient || !nextDate || sentWeek === weekKey) {
      continue;
    }
    if (nextDate < weekStart || nextDate > weekEnd) {
      continue;
    }

    if (!dueByRecipient[recipient]) {
      dueByRecipient[recipient] = [];
      rowNumbersByRecipient[recipient] = [];
    }
    dueByRecipient[recipient].push({
      store: value_(row, col, 'Store Name'),
      license: value_(row, col, 'License'),
      month: value_(row, col, 'Month'),
      nextDate: dateKey_(nextDate),
      cadence: value_(row, col, 'Cadence'),
      amount: value_(row, col, 'Committed Amount'),
      notes: value_(row, col, 'Notes'),
      person: value_(row, col, 'Person Contacted'),
      method: value_(row, col, 'Contact Method')
    });
    rowNumbersByRecipient[recipient].push(i + 1);
  }

  Object.keys(dueByRecipient).forEach(recipient => {
    const rows = dueByRecipient[recipient];
    const subject = `Weekly outreach reminders: ${formatDate_(weekStart)} - ${formatDate_(weekEnd)}`;
    const htmlBody = buildDigestHtml_(rows, weekStart, weekEnd);
    const textBody = rows.map(r =>
      `${r.store} (${r.license}) - due ${r.nextDate} - ${r.person || ''} ${r.method || ''} - ${r.notes || ''}`
    ).join('\n');

    GmailApp.sendEmail(recipient, subject, textBody, {
      cc: ALERT_CC,
      htmlBody: htmlBody,
      name: 'Balaclava Sales Dashboard'
    });

    rowNumbersByRecipient[recipient].forEach(rowNumber => {
      sheet.getRange(rowNumber, sentWeekCol).setValue(weekKey);
    });
  });
}

function buildDigestHtml_(rows, weekStart, weekEnd) {
  const trs = rows.map(r => `
    <tr>
      <td>${escapeHtml_(r.store)}</td>
      <td>${escapeHtml_(r.license)}</td>
      <td>${escapeHtml_(r.nextDate)}</td>
      <td>${escapeHtml_(r.person)}</td>
      <td>${escapeHtml_(r.method)}</td>
      <td>${escapeHtml_(r.cadence)}</td>
      <td>${escapeHtml_(r.amount)}</td>
      <td>${escapeHtml_(r.notes)}</td>
    </tr>
  `).join('');

  return `
    <p>Stores due for follow-up during ${formatDate_(weekStart)} - ${formatDate_(weekEnd)}.</p>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
      <thead>
        <tr>
          <th>Store</th><th>License</th><th>Due</th><th>Person</th>
          <th>Method</th><th>Cadence</th><th>Amount</th><th>Notes</th>
        </tr>
      </thead>
      <tbody>${trs}</tbody>
    </table>
  `;
}

function ensureColumn_(sheet, headers, name) {
  const existing = headers.indexOf(name);
  if (existing >= 0) {
    return existing + 1;
  }
  const nextCol = headers.length + 1;
  sheet.getRange(1, nextCol).setValue(name);
  return nextCol;
}

function indexMap_(headers) {
  const out = {};
  headers.forEach((h, i) => out[String(h)] = i);
  return out;
}

function value_(row, col, name) {
  const idx = col[name];
  if (idx === undefined) {
    return '';
  }
  return row[idx] === null || row[idx] === undefined ? '' : String(row[idx]).trim();
}

function parseDate_(value) {
  if (!value) {
    return null;
  }
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value)) {
    return value;
  }
  const parsed = new Date(value);
  return isNaN(parsed) ? null : parsed;
}

function startOfWeek_(date) {
  const out = new Date(date);
  const day = out.getDay();
  const diff = (day + 6) % 7;
  out.setDate(out.getDate() - diff);
  out.setHours(0, 0, 0, 0);
  return out;
}

function dateKey_(date) {
  return Utilities.formatDate(date, Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

function formatDate_(date) {
  return Utilities.formatDate(date, Session.getScriptTimeZone(), 'MMM d, yyyy');
}

function escapeHtml_(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
