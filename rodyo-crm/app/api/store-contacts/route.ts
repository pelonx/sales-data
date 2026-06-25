import { NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { createClient } from "@supabase/supabase-js";
import { DASHBOARD_DATA_TAG } from "@/lib/dashboard-data";

type StoreContactPayload = {
  storeId?: string;
  contactName?: string | null;
  phoneNumber?: string | null;
  email?: string | null;
};

function cleanOptionalText(value: unknown) {
  const cleaned = String(value ?? "").trim();
  return cleaned ? cleaned : null;
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

export async function POST(request: Request) {
  let payload: StoreContactPayload;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const storeId = cleanOptionalText(payload.storeId);
  if (!storeId) {
    return NextResponse.json({ error: "Missing storeId." }, { status: 400 });
  }

  try {
    const supabase = createSupabaseAdminClient();
    const { data, error } = await supabase
      .from("store_contacts")
      .upsert(
        {
          store_id: storeId,
          contact_name: cleanOptionalText(payload.contactName),
          phone_number: cleanOptionalText(payload.phoneNumber),
          email: cleanOptionalText(payload.email),
          updated_at: new Date().toISOString()
        },
        { onConflict: "store_id" }
      )
      .select("store_id, contact_name, phone_number, email")
      .single();

    if (error || !data) {
      return NextResponse.json(
        { error: error?.message || "Could not save buyer contact." },
        { status: 500 }
      );
    }

    revalidateTag(DASHBOARD_DATA_TAG, "max");

    return NextResponse.json({
      storeId: data.store_id,
      contactName: data.contact_name,
      phoneNumber: data.phone_number,
      email: data.email
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not save buyer contact." },
      { status: 500 }
    );
  }
}
