"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Map, SlidersHorizontal } from "lucide-react";
import type { DashboardSnapshot } from "@/lib/dashboard-data";
import { TERRITORY_MAP_COLORS, formatUsd, type StoreRollup } from "@/lib/rules";

type StoreDashboardProps = {
  snapshot: DashboardSnapshot;
};

type DetailTab = "contact" | "orders" | "buyer" | "history" | "samples";

const detailTabs: { id: DetailTab; label: string }[] = [
  { id: "contact", label: "Contact" },
  { id: "orders", label: "Orders" },
  { id: "buyer", label: "Buyer" },
  { id: "history", label: "History" },
  { id: "samples", label: "Samples" }
];

function CheckState({ active, label }: { active: boolean; label: string }) {
  return (
    <span className="tag" title={label}>
      <Check size={14} color={active ? "var(--green)" : "var(--muted)"} />
      {label}
    </span>
  );
}

function summarizeStores(stores: StoreRollup[]) {
  return {
    totalRetailers: stores.length,
    mappedStores: stores.filter((store) => (
      Number.isFinite(store.latitude) && Number.isFinite(store.longitude)
    )).length,
    lapsedPriority: stores.filter((store) => store.mapCategory.startsWith("K Savage Lapsed")).length,
    openLanePriority: stores.filter((store) => store.mapCategory.startsWith("Open Lane")).length,
    pitchMayfield: stores.filter((store) => store.mapCategory === "Pitch Mayfield").length
  };
}

function storeKey(store: StoreRollup) {
  return store.storeId || store.licenseKey || store.license;
}

function formatDate(value?: string | null) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(date);
}

function DetailStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="detail-row">
      <span>{label}</span>
      <strong>{value || "-"}</strong>
    </div>
  );
}

function StoreDetailContent({ activeTab, store }: { activeTab: DetailTab; store: StoreRollup }) {
  if (activeTab === "orders") {
    return (
      <div className="detail-stack">
        <div className="metrics detail-metrics">
          <DetailStat label="Orders" value={store.orders.toLocaleString()} />
          <DetailStat label="Brand Revenue" value={formatUsd(store.brandRevenue)} />
        </div>
        <div className="detail-list">
          <DetailRow label="Last order" value={formatDate(store.lastOrderAt)} />
          <DetailRow label="Order #" value={store.lastOrderNumber} />
          <DetailRow label="K. Savage last order" value={formatDate(store.kSavageLastOrderAt)} />
          <DetailRow label="K. Savage history" value={formatUsd(store.kSavageHistoricalRevenue)} />
          <DetailRow label="Mayfield active" value={formatUsd(store.mayfieldActiveRevenue)} />
          <DetailRow label="Leisure Land active" value={formatUsd(store.leisureLandActiveRevenue)} />
        </div>
      </div>
    );
  }

  if (activeTab === "buyer") {
    return (
      <div className="detail-stack">
        <div className="detail-list">
          <DetailRow label="Buyer" value={store.contactName} />
          <DetailRow label="Phone" value={store.phoneNumber} />
          <DetailRow label="Email" value={store.email} />
          <DetailRow label="License" value={store.license} />
          <DetailRow label="Rep" value={store.territoryRep} />
          <DetailRow label="County" value={store.county} />
          <DetailRow label="Location" value={[store.city, store.state, store.zip].filter(Boolean).join(", ")} />
        </div>
      </div>
    );
  }

  if (activeTab === "history") {
    return (
      <div className="detail-stack">
        <div className="detail-tabs">
          <CheckState active={store.hasContactEver} label="Any log" />
          <CheckState active={store.hasContactThisMonth} label="This month" />
          <CheckState active={store.hasContactThisWeek} label="This week" />
        </div>
        <div className="detail-list">
          <DetailRow label="Log count" value={store.contactLogCount.toLocaleString()} />
          <DetailRow label="Last contact" value={formatDate(store.lastContactDate)} />
          <DetailRow label="Method" value={store.lastContactMethod} />
          <DetailRow label="Person" value={store.lastContactPerson} />
        </div>
        {store.lastContactNotes ? <p className="detail-note">{store.lastContactNotes}</p> : null}
      </div>
    );
  }

  if (activeTab === "samples") {
    return (
      <div className="detail-stack">
        <div className="metrics detail-metrics">
          <DetailStat label="Sample Drops" value={store.sampleDropCount.toLocaleString()} />
          <DetailStat label="Latest Drop" value={formatDate(store.latestSampleDate)} />
        </div>
        <div className="detail-list">
          <DetailRow label="Brand" value={store.latestSampleBrand} />
          <DetailRow label="Product" value={store.latestSampleProduct} />
        </div>
      </div>
    );
  }

  return (
    <div className="detail-stack">
      <div className="metrics detail-metrics">
        <DetailStat label="Latest Month" value={formatUsd(store.latestMonthRevenue)} />
        <DetailStat label="Market Sales" value={formatUsd(store.marketSalesLastMonth)} />
      </div>
      <div className="detail-tabs">
        <CheckState active={store.hasContactEver} label="Any log" />
        <CheckState active={store.hasContactThisMonth} label="This month" />
        <CheckState active={store.hasContactThisWeek} label="This week" />
      </div>
      <div className="form-grid">
        <div className="field">
          <label>Contact method</label>
          <select defaultValue="">
            <option value="">Select</option>
            <option>In-person</option>
            <option>Phone</option>
            <option>Email</option>
          </select>
        </div>
        <div className="field">
          <label>Initials</label>
          <select defaultValue="">
            <option value="">Select</option>
            <option>DK</option>
            <option>CH</option>
          </select>
        </div>
      </div>
      <button className="primary-button" type="button">
        Save Contact Log
      </button>
    </div>
  );
}

export function StoreDashboard({ snapshot }: StoreDashboardProps) {
  const [storeQuery, setStoreQuery] = useState("");
  const [activeTab, setActiveTab] = useState<DetailTab>("contact");
  const [selectedStoreKey, setSelectedStoreKey] = useState(() => (
    snapshot.stores[0] ? storeKey(snapshot.stores[0]) : ""
  ));
  const normalizedStoreQuery = storeQuery.trim().toLowerCase();
  const filteredStores = useMemo(() => {
    if (!normalizedStoreQuery) {
      return snapshot.stores;
    }
    return snapshot.stores.filter((store) => (
      store.storeName.toLowerCase().includes(normalizedStoreQuery) ||
      store.license.toLowerCase().includes(normalizedStoreQuery) ||
      store.licenseKey.toLowerCase().includes(normalizedStoreQuery)
    ));
  }, [normalizedStoreQuery, snapshot.stores]);
  const metrics = useMemo(() => summarizeStores(filteredStores), [filteredStores]);
  const selectedStore = filteredStores.find((store) => storeKey(store) === selectedStoreKey) || filteredStores[0];
  const rowMeta = normalizedStoreQuery
    ? `${filteredStores.length.toLocaleString()} of ${snapshot.stores.length.toLocaleString()} rows`
    : `${filteredStores.length.toLocaleString()} rows`;

  useEffect(() => {
    if (!filteredStores.length) {
      setSelectedStoreKey("");
      return;
    }
    if (!filteredStores.some((store) => storeKey(store) === selectedStoreKey)) {
      setSelectedStoreKey(storeKey(filteredStores[0]));
    }
  }, [filteredStores, selectedStoreKey]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-logo" src="/logo.png" alt="RODYO" />
          <span>Balaclava store operations</span>
        </div>
        <nav className="nav" aria-label="Main navigation">
          <a className="active" href="/">
            Stores
          </a>
          <a href="/">Map</a>
          <a href="/">Orders</a>
          <a href="/">Goals</a>
          <a href="/">Sync</a>
        </nav>
      </aside>

      <main className="main">
        <section className="toolbar">
          <div className="toolbar-title">
            <div>
              <h2>Stores</h2>
              <div className="caption">
                {snapshot.source === "demo"
                  ? "Demo shell. Connect Supabase to load live CRM data."
                  : "Live Supabase data"}
              </div>
            </div>
            <button className="primary-button" type="button">
              <Map size={16} /> Launch Map
            </button>
          </div>

          <form className="filters" aria-label="Store filters" onSubmit={(event) => event.preventDefault()}>
            <div className="field store-filter-field">
              <label>Stores</label>
              <input
                type="search"
                value={storeQuery}
                onChange={(event) => setStoreQuery(event.target.value)}
                placeholder="Name or license"
              />
            </div>
            <div className="field">
              <label>Balaclava Sales</label>
              <select defaultValue="all">
                <option value="all">Any range</option>
                <option value="1000">$1k+</option>
                <option value="5000">$5k+</option>
              </select>
            </div>
            <div className="field">
              <label>Store Revenue</label>
              <select defaultValue="all">
                <option value="all">Any range</option>
                <option value="50000">$50k+</option>
                <option value="100000">$100k+</option>
              </select>
            </div>
            <div className="field">
              <label>Pareto</label>
              <select defaultValue="all">
                <option value="all">All stores</option>
                <option value="top30">Top 30</option>
                <option value="eighty">80% revenue set</option>
              </select>
            </div>
            <div className="field">
              <label>Priority</label>
              <select defaultValue="all">
                <option value="all">All priorities</option>
                <option value="lapsed">Lapsed</option>
                <option value="open-lane">Open lane</option>
              </select>
            </div>
            <div className="field">
              <label>Region</label>
              <select defaultValue="all">
                <option value="all">All regions</option>
              </select>
            </div>
            <button className="primary-button" type="button">
              <SlidersHorizontal size={16} /> Apply
            </button>
          </form>
        </section>

        <section className="metrics">
          <div className="metric">
            <div className="metric-label">Retailers</div>
            <div className="metric-value">{metrics.totalRetailers.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Mapped</div>
            <div className="metric-value">{metrics.mappedStores.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Lapsed Priority</div>
            <div className="metric-value">{metrics.lapsedPriority.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Open Lane</div>
            <div className="metric-value">{metrics.openLanePriority.toLocaleString()}</div>
          </div>
          <div className="metric">
            <div className="metric-label">Pitch Mayfield</div>
            <div className="metric-value">{metrics.pitchMayfield.toLocaleString()}</div>
          </div>
        </section>

        <section className="content-grid">
          <div className="panel">
            <div className="panel-header">
              <h3>Filtered Stores</h3>
              <span className="table-meta">{rowMeta}</span>
            </div>
            <table className="store-table">
              <thead>
                <tr>
                  <th style={{ width: "34%" }}>Store</th>
                  <th>Designation</th>
                  <th>Balaclava</th>
                  <th>Store Revenue</th>
                  <th>Rep</th>
                  <th>Log</th>
                </tr>
              </thead>
              <tbody>
                {filteredStores.map((store) => (
                  <tr
                    key={storeKey(store)}
                    className={selectedStore && storeKey(store) === storeKey(selectedStore) ? "is-selected" : ""}
                    tabIndex={0}
                    onClick={() => setSelectedStoreKey(storeKey(store))}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelectedStoreKey(storeKey(store));
                      }
                    }}
                  >
                    <td>
                      <div className="store-name">{store.storeName}</div>
                      <div className="store-subtext">
                        {store.license} · {store.city || "No city"} {store.zip || ""}
                      </div>
                    </td>
                    <td>
                      <span className="tag">
                        <span
                          className="dot"
                          style={{
                            background: TERRITORY_MAP_COLORS[store.mapCategory] ?? "var(--muted)"
                          }}
                        />
                        {store.mapCategory}
                      </span>
                    </td>
                    <td>{formatUsd(store.latestMonthRevenue)}</td>
                    <td>{formatUsd(store.marketSalesLastMonth)}</td>
                    <td>{store.territoryRep || "-"}</td>
                    <td>{store.hasContactEver ? "✅" : ""}</td>
                  </tr>
                ))}
                {!filteredStores.length ? (
                  <tr>
                    <td colSpan={6}>No stores match that search.</td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>

          <aside className="panel store-detail">
            <div className="detail-title">
              <h3>{selectedStore?.storeName ?? "Select a store"}</h3>
              <span className="caption">
                {selectedStore ? `${selectedStore.license} · ${selectedStore.city ?? ""}` : "Store detail drawer"}
              </span>
            </div>
            <div className="detail-tabs" role="tablist" aria-label="Store detail sections">
              {detailTabs.map((tab) => (
                <button
                  key={tab.id}
                  className={activeTab === tab.id ? "active" : ""}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab.id}
                  onClick={() => setActiveTab(tab.id)}
                >
                  {tab.label}
                </button>
              ))}
            </div>
            {selectedStore ? <StoreDetailContent activeTab={activeTab} store={selectedStore} /> : null}
          </aside>
        </section>
      </main>
    </div>
  );
}
