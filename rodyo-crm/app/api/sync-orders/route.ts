import { NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { createClient } from "@supabase/supabase-js";
import { DASHBOARD_DATA_TAG } from "@/lib/dashboard-data";

const DEFAULT_SHEET_URL = "https://docs.google.com/spreadsheets/d/1kY5e6SXd7eQ7GJx-jg6M1R60WCCZ9I_25Eb7ZmuDKHw/edit?usp=sharing";
const DEFAULT_ORDER_SHEET_NAME = "Cultivera Data";
const SHEET_ID_PATTERN = /\/spreadsheets\/d\/([a-zA-Z0-9_-]+)/;
const TERRITORY_BRANDS = ["K. Savage", "Mayfield", "Leisure Land"] as const;

type CsvRow = Record<string, string>;

type StoreRow = {
  id: string;
  license: string | null;
  license_key: string | null;
  store_name: string | null;
  store_key: string | null;
};

type ImportedOrderRow = {
  id: string;
  order_number: string;
  license_key: string | null;
};

function createSupabaseAdminClient() {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_SECRET_KEY || process.env.SUPABASE_SERVICE_ROLE_KEY;

  if (!supabaseUrl || !supabaseKey) {
    throw new Error("Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SECRET_KEY.");
  }

  return createClient(supabaseUrl, supabaseKey, {
    auth: {
      persistSession: false
    }
  });
}

function cleanCell(value: unknown) {
  const text = String(value ?? "").trim();
  return text && !["nan", "none", "null"].includes(text.toLowerCase()) ? text : "";
}

function cleanReference(value: unknown) {
  const text = cleanCell(value);
  if (!text) {
    return "";
  }
  const number = Number(text);
  if (Number.isFinite(number) && Number.isInteger(number)) {
    return String(number);
  }
  return text;
}

function licenseMatchKey(value: unknown) {
  let text = cleanReference(value).toUpperCase();
  if (!text) {
    return "";
  }
  text = text.replace(/^(LICENSE|LIC)\s*#?\s*/, "");
  text = text.replace(/[^A-Z0-9]/g, "");
  text = text.replace(/^(LICENSE|LIC)/, "");
  return /^\d+$/.test(text) ? (text.replace(/^0+/, "") || "0") : text;
}

function parseAmount(value: unknown) {
  const cleaned = String(value ?? "").replace(/[$,\s]/g, "");
  if (!cleaned || ["nan", "none", "null"].includes(cleaned.toLowerCase())) {
    return 0;
  }
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : 0;
}

function parseTimestamp(value: unknown) {
  const cleaned = cleanCell(value);
  if (!cleaned) {
    return null;
  }
  const timestamp = new Date(cleaned);
  return Number.isNaN(timestamp.getTime()) ? null : timestamp.toISOString();
}

function brandFromSubProductLine(value: unknown) {
  const text = cleanCell(value);
  if (text.startsWith("LL")) return "Leisure Land";
  if (text.startsWith("MF")) return "Mayfield";
  if (text.startsWith("KS")) return "K. Savage";
  if (text.startsWith("Bulk")) return "Bulk";
  return "Other";
}

function firstSourceColumn(row: CsvRow, aliases: string[]) {
  const columns = new Map(Object.keys(row).map((key) => [key.trim().toLowerCase(), key]));
  for (const alias of aliases) {
    const found = columns.get(alias.trim().toLowerCase());
    if (found) {
      return found;
    }
  }
  return "";
}

function rowPayload(row: CsvRow) {
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, cleanCell(value)])
  );
}

function parseCsv(text: string) {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const nextChar = text[index + 1];

    if (char === "\"") {
      if (inQuotes && nextChar === "\"") {
        cell += "\"";
        index += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (char === "," && !inQuotes) {
      row.push(cell);
      cell = "";
      continue;
    }

    if ((char === "\n" || char === "\r") && !inQuotes) {
      if (char === "\r" && nextChar === "\n") {
        index += 1;
      }
      row.push(cell);
      if (row.some((value) => cleanCell(value))) {
        rows.push(row);
      }
      row = [];
      cell = "";
      continue;
    }

    cell += char;
  }

  row.push(cell);
  if (row.some((value) => cleanCell(value))) {
    rows.push(row);
  }

  if (!rows.length) {
    return [];
  }

  const headers = rows[0].map((header) => cleanCell(header));
  return rows.slice(1).map((values) => {
    const record: CsvRow = {};
    headers.forEach((header, index) => {
      if (header) {
        record[header] = cleanCell(values[index]);
      }
    });
    return record;
  });
}

function googleSheetCsvUrlBySheetName(sheetUrl: string, sheetName: string) {
  const parsed = new URL(sheetUrl);
  if (!parsed.hostname.includes("docs.google.com")) {
    return sheetUrl;
  }
  const match = SHEET_ID_PATTERN.exec(parsed.pathname);
  if (!match) {
    throw new Error("Could not find spreadsheet ID in order sheet URL.");
  }
  return `https://docs.google.com/spreadsheets/d/${match[1]}/gviz/tq?tqx=out:csv&sheet=${encodeURIComponent(sheetName)}`;
}

async function fetchOrderSheet() {
  const sheetUrl = process.env.IMPORT_GOOGLE_SHEET_URL || process.env.ORDER_SHEET_URL || DEFAULT_SHEET_URL;
  const sheetName = process.env.IMPORT_ORDER_SHEET_NAME || process.env.ORDER_SHEET_NAME || DEFAULT_ORDER_SHEET_NAME;
  const response = await fetch(googleSheetCsvUrlBySheetName(sheetUrl, sheetName), {
    cache: "no-store",
    headers: {
      "User-Agent": "RODYO CRM order sync"
    }
  });

  if (!response.ok) {
    throw new Error(`Could not fetch Cultivera order sheet. Google returned HTTP ${response.status}.`);
  }

  const text = await response.text();
  const rows = parseCsv(text);
  if (!rows.length) {
    throw new Error("The Cultivera order sheet is empty.");
  }
  return rows;
}

function chunk<T>(values: T[], size: number) {
  const chunks: T[][] = [];
  for (let index = 0; index < values.length; index += size) {
    chunks.push(values.slice(index, index + size));
  }
  return chunks;
}

function orderKey(orderNumber: string, licenseKey: string) {
  return `${orderNumber}::${licenseKey}`;
}

// Allow the sync enough time to paginate and upsert thousands of rows when run
// on a serverless host (Vercel caps non-configured functions well below this).
export const maxDuration = 60;

async function runOrderSync() {
  try {
    const rows = await fetchOrderSheet();
    const sample = rows[0] || {};
    const orderNumberCol = firstSourceColumn(sample, ["Order #", "Order Number", "Order"]);
    const clientCol = firstSourceColumn(sample, ["Client", "Store Name", "Customer"]);
    const licenseCol = firstSourceColumn(sample, ["License #", "License"]);
    const submittedCol = firstSourceColumn(sample, ["Submitted Date", "Order Date", "Date"]);
    const statusCol = firstSourceColumn(sample, ["Status", "Order Status"]);
    const brandCol = firstSourceColumn(sample, ["Brand"]);
    const productCol = firstSourceColumn(sample, ["Product", "Product Name", "Inventory Name", "Item"]);
    const subProductCol = firstSourceColumn(sample, ["Sub Product Line", "Subproduct Line"]);
    const unitsCol = firstSourceColumn(sample, ["Units", "Quantity", "Qty"]);
    const lineTotalCol = firstSourceColumn(sample, ["Line Total", "Sales", "Total"]);

    if (!orderNumberCol || !licenseCol) {
      return NextResponse.json(
        { error: "Cultivera orders need at least Order # and License # columns." },
        { status: 400 }
      );
    }

    const supabase = createSupabaseAdminClient();
    const { data: stores, error: storesError } = await supabase
      .from("stores")
      .select("id, license, license_key, store_name, store_key");

    if (storesError) {
      throw new Error(storesError.message);
    }

    const storesByLicenseKey = new Map<string, StoreRow>();
    (stores || []).forEach((store) => {
      const key = cleanCell(store.license_key);
      if (key) {
        storesByLicenseKey.set(key, store as StoreRow);
      }
    });

    const orderRowsByKey = new Map<string, Record<string, unknown>>();
    let skippedRows = 0;
    const syncedAt = new Date().toISOString();

    rows.forEach((row) => {
      const orderNumber = cleanReference(row[orderNumberCol]);
      const license = cleanReference(row[licenseCol]);
      const licenseKey = licenseMatchKey(license);
      if (!orderNumber) {
        skippedRows += 1;
        return;
      }
      const key = orderKey(orderNumber, licenseKey);
      if (orderRowsByKey.has(key)) {
        return;
      }
      const store = storesByLicenseKey.get(licenseKey);
      orderRowsByKey.set(key, {
        order_number: orderNumber,
        store_id: store?.id ?? null,
        client_name: clientCol ? cleanCell(row[clientCol]) : "",
        license,
        license_key: licenseKey,
        submitted_at: submittedCol ? parseTimestamp(row[submittedCol]) : null,
        status: statusCol ? cleanCell(row[statusCol]) : "",
        source_name: "cultivera",
        imported_at: syncedAt,
        raw_payload: rowPayload(row)
      });
    });

    const orderRows = [...orderRowsByKey.values()];
    const importedOrders = new Map<string, ImportedOrderRow>();

    for (const rowsChunk of chunk(orderRows, 500)) {
      const { data, error } = await supabase
        .from("orders")
        .upsert(rowsChunk, { onConflict: "order_number,license_key" })
        .select("id, order_number, license_key");

      if (error) {
        throw new Error(error.message);
      }
      (data || []).forEach((order) => {
        importedOrders.set(orderKey(order.order_number, order.license_key || ""), order as ImportedOrderRow);
      });
    }

    const orderIds = [...new Set([...importedOrders.values()].map((order) => order.id))];
    for (const idsChunk of chunk(orderIds, 500)) {
      const { error } = await supabase
        .from("order_items")
        .delete()
        .in("order_id", idsChunk);

      if (error) {
        throw new Error(error.message);
      }
    }

    const itemRows: Record<string, unknown>[] = [];
    rows.forEach((row) => {
      const orderNumber = cleanReference(row[orderNumberCol]);
      const licenseKey = licenseMatchKey(row[licenseCol]);
      const order = importedOrders.get(orderKey(orderNumber, licenseKey));
      if (!order) {
        return;
      }
      const subProductLine = subProductCol ? cleanCell(row[subProductCol]) : "";
      const explicitBrand = brandCol ? cleanCell(row[brandCol]) : "";
      itemRows.push({
        order_id: order.id,
        brand: explicitBrand || brandFromSubProductLine(subProductLine),
        product_name: productCol ? cleanCell(row[productCol]) : "",
        sub_product_line: subProductLine,
        units: unitsCol ? parseAmount(row[unitsCol]) : 0,
        line_total: lineTotalCol ? parseAmount(row[lineTotalCol]) : 0,
        raw_payload: rowPayload(row)
      });
    });

    for (const itemsChunk of chunk(itemRows, 500)) {
      const { error } = await supabase
        .from("order_items")
        .insert(itemsChunk);

      if (error) {
        throw new Error(error.message);
      }
    }

    const brandTotals: Record<string, number> = Object.fromEntries(TERRITORY_BRANDS.map((brand) => [brand, 0]));
    itemRows.forEach((item) => {
      const brand = String(item.brand || "");
      if (brand in brandTotals) {
        brandTotals[brand] += Number(item.line_total || 0);
      }
    });

    return {
      orderRows: orderRows.length,
      itemRows: itemRows.length,
      skippedRows,
      brandTotals,
      syncedAt
    };
  } catch (error) {
    throw error instanceof Error ? error : new Error("Could not sync orders.");
  }
}

async function handleSync() {
  const result = await runOrderSync();
  // Bust the cached dashboard snapshot so freshly synced orders show immediately.
  revalidateTag(DASHBOARD_DATA_TAG, "max");
  return NextResponse.json(result);
}

export async function POST() {
  try {
    return await handleSync();
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not sync orders." },
      { status: 500 }
    );
  }
}

// Scheduled entry point (Vercel Cron / external scheduler). Vercel Cron sends
// `Authorization: Bearer <CRON_SECRET>` automatically when CRON_SECRET is set;
// a `?secret=` query param is also accepted for other schedulers. Without a
// configured secret the GET trigger stays disabled so it can't be hit openly.
export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) {
    return NextResponse.json(
      { error: "Scheduled sync is not configured. Set CRON_SECRET to enable it." },
      { status: 401 }
    );
  }

  const headerToken = (request.headers.get("authorization") || "").replace(/^Bearer\s+/i, "");
  const queryToken = new URL(request.url).searchParams.get("secret") || "";
  if (headerToken !== secret && queryToken !== secret) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }

  try {
    return await handleSync();
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not sync orders." },
      { status: 500 }
    );
  }
}
