import { NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { createClient } from "@supabase/supabase-js";
import { DASHBOARD_DATA_TAG } from "@/lib/dashboard-data";

type StoreNamePayload = {
  storeId?: string;
  storeName?: string | null;
};

function cleanText(value: unknown) {
  return String(value ?? "").trim();
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
  let payload: StoreNamePayload;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const storeId = cleanText(payload.storeId);
  if (!storeId) {
    return NextResponse.json({ error: "Missing storeId." }, { status: 400 });
  }

  const storeName = cleanText(payload.storeName);
  if (!storeName) {
    return NextResponse.json({ error: "Store name can’t be empty." }, { status: 400 });
  }

  try {
    const supabase = createSupabaseAdminClient();
    const { data, error } = await supabase
      .from("stores")
      .update({ store_name: storeName })
      .eq("id", storeId)
      .select("id, store_name")
      .single();

    if (error || !data) {
      return NextResponse.json(
        { error: error?.message || "Could not save store name." },
        { status: 500 }
      );
    }

    revalidateTag(DASHBOARD_DATA_TAG, "max");

    return NextResponse.json({
      storeId: data.id,
      storeName: data.store_name
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not save store name." },
      { status: 500 }
    );
  }
}
