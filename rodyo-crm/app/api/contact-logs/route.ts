import { NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { createClient } from "@supabase/supabase-js";
import { DASHBOARD_DATA_TAG } from "@/lib/dashboard-data";

type ContactLogPayload = {
  storeId?: string;
  license?: string | null;
  licenseKey?: string | null;
  storeName?: string | null;
  dateContacted?: string | null;
  contactMethod?: string | null;
  initials?: string | null;
  personContacted?: string | null;
  notes?: string | null;
};

function cleanOptionalText(value: unknown) {
  const cleaned = String(value ?? "").trim();
  return cleaned ? cleaned : null;
}

function localDateValue(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function cleanDate(value: unknown) {
  const cleaned = cleanOptionalText(value) || localDateValue();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(cleaned)) {
    return "";
  }
  const parsed = new Date(`${cleaned}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? "" : cleaned;
}

function monthStart(dateValue: string) {
  return `${dateValue.slice(0, 7)}-01`;
}

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

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const storeId = cleanOptionalText(searchParams.get("storeId"));
  const licenseKey = cleanOptionalText(searchParams.get("licenseKey"));

  if (!storeId && !licenseKey) {
    return NextResponse.json({ error: "Provide storeId or licenseKey." }, { status: 400 });
  }

  try {
    const supabase = createSupabaseAdminClient();
    const orFilters = [
      storeId ? `store_id.eq.${storeId}` : "",
      licenseKey ? `license_key.eq.${licenseKey}` : ""
    ].filter(Boolean);

    const { data, error } = await supabase
      .from("contact_logs")
      .select("id, store_id, license_key, date_contacted, contact_method, initials, person_contacted, notes, saved_at")
      .or(orFilters.join(","))
      .order("date_contacted", { ascending: false, nullsFirst: false })
      .order("saved_at", { ascending: false })
      .limit(200);

    if (error) {
      return NextResponse.json({ error: error.message }, { status: 500 });
    }

    const logs = (data || []).map((row) => ({
      id: row.id,
      storeId: row.store_id,
      dateContacted: row.date_contacted,
      contactMethod: row.contact_method,
      initials: row.initials,
      personContacted: row.person_contacted,
      notes: row.notes,
      savedAt: row.saved_at
    }));

    return NextResponse.json({ logs });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not load contact logs." },
      { status: 500 }
    );
  }
}

export async function POST(request: Request) {
  let payload: ContactLogPayload;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const storeId = cleanOptionalText(payload.storeId);
  if (!storeId) {
    return NextResponse.json({ error: "Missing storeId." }, { status: 400 });
  }

  const dateContacted = cleanDate(payload.dateContacted);
  if (!dateContacted) {
    return NextResponse.json({ error: "Date contacted must use YYYY-MM-DD format." }, { status: 400 });
  }

  try {
    const supabase = createSupabaseAdminClient();
    const { data, error } = await supabase
      .from("contact_logs")
      .insert({
        store_id: storeId,
        license: cleanOptionalText(payload.license),
        license_key: cleanOptionalText(payload.licenseKey),
        store_name: cleanOptionalText(payload.storeName),
        contact_month: monthStart(dateContacted),
        date_contacted: dateContacted,
        contact_method: cleanOptionalText(payload.contactMethod),
        initials: cleanOptionalText(payload.initials),
        person_contacted: cleanOptionalText(payload.personContacted),
        notes: cleanOptionalText(payload.notes),
        saved_at: new Date().toISOString()
      })
      .select("id, store_id, date_contacted, contact_method, initials, person_contacted, notes, saved_at")
      .single();

    if (error || !data) {
      return NextResponse.json(
        { error: error?.message || "Could not save contact log." },
        { status: 500 }
      );
    }

    revalidateTag(DASHBOARD_DATA_TAG, "max");

    return NextResponse.json({
      id: data.id,
      storeId: data.store_id,
      dateContacted: data.date_contacted,
      contactMethod: data.contact_method,
      initials: data.initials,
      personContacted: data.person_contacted,
      notes: data.notes,
      savedAt: data.saved_at
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not save contact log." },
      { status: 500 }
    );
  }
}
