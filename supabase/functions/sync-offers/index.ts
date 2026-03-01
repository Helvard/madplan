/**
 * Supabase Edge Function: sync-offers
 *
 * Fetches current Rema 1000 discounts from the Algolia API and replaces
 * the `offers` table in Supabase atomically (delete-all → insert-fresh).
 *
 * Triggered by a pg_cron job every Monday at 04:00 UTC, or manually via HTTP.
 * Requires CRON_SECRET env var (set via `supabase secrets set CRON_SECRET=...`).
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

// ── Algolia config (Rema 1000's public read-only search API) ──────────────────
const ALGOLIA_APP_ID = "FLWDN2189E";
const ALGOLIA_API_KEY = "fa20981a63df668e871a87a8fbd0caed";
const ALGOLIA_INDEX = "aws-prod-products";
const ALGOLIA_URL = `https://flwdn2189e-dsn.algolia.net/1/indexes/${ALGOLIA_INDEX}/query`;

// ── Types ─────────────────────────────────────────────────────────────────────
interface Offer {
  product_id: string;
  name: string;
  underline: string | null;
  price: string;
  price_numeric: number;
  normal_price: string | null;
  savings_percent: number;
  price_per_unit: string | null;
  department: string | null;
  category: string | null;
  scraped_at: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatPrice(price: number | null): string {
  if (!price) return "";
  return `${price.toFixed(2)} kr`.replace(".", ",");
}

async function fetchOffers(limit = 500): Promise<Offer[]> {
  const params = [
    "query=",
    `length=${limit}`,
    "offset=0",
    "clickAnalytics=true",
    'facetFilters=[["labels:on_discount"]]',
    'facets=["labels"]',
  ].join("&");

  const url = new URL(ALGOLIA_URL);
  url.searchParams.set("x-algolia-agent", "Algolia for vanilla JavaScript 3.21.1");
  url.searchParams.set("x-algolia-application-id", ALGOLIA_APP_ID);
  url.searchParams.set("x-algolia-api-key", ALGOLIA_API_KEY);

  const response = await fetch(url.toString(), {
    method: "POST",
    headers: { accept: "application/json", "content-type": "application/json" },
    body: JSON.stringify({ params }),
  });

  if (!response.ok) {
    throw new Error(`Algolia responded with ${response.status}: ${await response.text()}`);
  }

  // deno-lint-ignore no-explicit-any
  const data: any = await response.json();

  // deno-lint-ignore no-explicit-any
  return (data.hits ?? []).map((hit: any): Offer => {
    const pricing = hit.pricing ?? {};
    const normalPrice: number = pricing.normal_price ?? 0;
    const salePrice: number = pricing.price ?? 0;
    const savingsPct =
      normalPrice && salePrice
        ? Math.round((1 - salePrice / normalPrice) * 1000) / 10
        : 0;

    return {
      product_id: String(hit.objectID),
      name: hit.name ?? "",
      underline: hit.underline ?? null,
      price: formatPrice(salePrice),
      price_numeric: salePrice,
      normal_price: normalPrice ? formatPrice(normalPrice) : null,
      savings_percent: savingsPct,
      price_per_unit: pricing.price_per_unit ?? null,
      department: hit.department_name ?? null,
      category: hit.category_name ?? null,
      scraped_at: new Date().toISOString(),
    };
  });
}

// ── Entry point ───────────────────────────────────────────────────────────────
Deno.serve(async (req: Request) => {
  // Verify secret so only the pg_cron job (or an admin) can trigger this.
  const cronSecret = Deno.env.get("CRON_SECRET");
  if (cronSecret) {
    const auth = req.headers.get("authorization") ?? "";
    if (auth !== `Bearer ${cronSecret}`) {
      return new Response(JSON.stringify({ error: "Unauthorized" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      });
    }
  }

  const supabase = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );

  try {
    console.log("Fetching offers from Algolia...");
    const offers = await fetchOffers(500);
    console.log(`Fetched ${offers.length} discounted products.`);

    if (!offers.length) {
      return new Response(
        JSON.stringify({ error: "No offers fetched — aborting to avoid wiping the table." }),
        { status: 500, headers: { "content-type": "application/json" } },
      );
    }

    // Atomic replace: delete all existing rows first, then insert fresh data.
    const { error: deleteError } = await supabase
      .from("offers")
      .delete()
      .neq("product_id", "");
    if (deleteError) throw deleteError;

    // Supabase has a default row limit per request; batch in chunks of 200.
    let inserted = 0;
    for (let i = 0; i < offers.length; i += 200) {
      const chunk = offers.slice(i, i + 200);
      const { error: insertError } = await supabase.from("offers").insert(chunk);
      if (insertError) throw insertError;
      inserted += chunk.length;
    }

    console.log(`Sync complete. Inserted ${inserted} offers.`);
    return new Response(
      JSON.stringify({ success: true, inserted, fetched: offers.length }),
      { headers: { "content-type": "application/json" } },
    );
  } catch (err) {
    console.error("Sync failed:", err);
    return new Response(
      JSON.stringify({ error: String(err) }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }
});
