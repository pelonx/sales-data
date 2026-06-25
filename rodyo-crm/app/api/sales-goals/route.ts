import { NextResponse } from "next/server";
import { revalidateTag } from "next/cache";
import { createClient } from "@supabase/supabase-js";
import { DASHBOARD_DATA_TAG } from "@/lib/dashboard-data";

type SalesGoalPayloadRow = {
  goalType?: string;
  weekId?: string | null;
  weekLabel?: string | null;
  brand?: string | null;
  goalAmount?: number | string | null;
  notes?: string | null;
};

type SalesGoalPayload = {
  month?: string;
  rows?: SalesGoalPayloadRow[];
};

function cleanOptionalText(value: unknown) {
  const cleaned = String(value ?? "").trim();
  return cleaned ? cleaned : null;
}

function cleanGoalAmount(value: unknown) {
  const parsed = Number(String(value ?? "").replace(/[$,\s]/g, ""));
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0;
}

function monthStart(value: unknown) {
  const cleaned = String(value ?? "").trim();
  if (!/^\d{4}-\d{2}$/.test(cleaned)) {
    return "";
  }
  return `${cleaned}-01`;
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
  let payload: SalesGoalPayload;

  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON payload." }, { status: 400 });
  }

  const goalMonth = monthStart(payload.month);
  if (!goalMonth) {
    return NextResponse.json({ error: "Missing month in YYYY-MM format." }, { status: 400 });
  }

  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const updatedAt = new Date().toISOString();
  const insertRows = rows
    .map((row) => ({
      goal_month: goalMonth,
      goal_type: cleanOptionalText(row.goalType) || "",
      week_id: cleanOptionalText(row.weekId),
      week_label: cleanOptionalText(row.weekLabel),
      brand: cleanOptionalText(row.brand),
      goal_amount: cleanGoalAmount(row.goalAmount),
      notes: cleanOptionalText(row.notes),
      updated_at: updatedAt
    }))
    .filter((row) => (
      row.goal_type &&
      (row.goal_amount > 0 || Boolean(row.notes))
    ));

  try {
    const supabase = createSupabaseAdminClient();
    const { error: deleteError } = await supabase
      .from("sales_goals")
      .delete()
      .eq("goal_month", goalMonth);

    if (deleteError) {
      throw new Error(deleteError.message);
    }

    if (insertRows.length) {
      const { error: insertError } = await supabase
        .from("sales_goals")
        .insert(insertRows);

      if (insertError) {
        throw new Error(insertError.message);
      }
    }

    revalidateTag(DASHBOARD_DATA_TAG, "max");

    return NextResponse.json({
      month: goalMonth,
      rows: insertRows.length,
      savedAt: updatedAt
    });
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Could not save goals." },
      { status: 500 }
    );
  }
}
