let editMode = false,
  initialized = false;
const collapsed = new Set();
const token = prompt("Enter the admin token"),
  auth = {
    Authorization: "Bearer " + token,
    "Content-Type": "application/json",
  },
  $ = (s) => document.querySelector(s),
  esc = (v) =>
    String(v ?? "").replace(
      /[&<>'"]/g,
      (c) =>
        ({
          "&": "&amp;",
          "<": "&lt;",
          ">": "&gt;",
          "'": "&#39;",
          '"': "&quot;",
        })[c],
    );
async function req(url, body) {
  const r = await fetch(url, {
      method: body ? "POST" : "GET",
      headers: auth,
      body: body ? JSON.stringify(body) : undefined,
    }),
    d = await r.json();
  if (!r.ok) throw Error(d.error || "Request failed");
  return d;
}
async function publicGet(url) {
  const r = await fetch(url, { cache: "no-store" });
  const d = await r.json();
  if (!r.ok) throw Error(d.error || "Request failed");
  return d;
}
function withdrawalMatch(w, rider) {
  if (!w || !rider) return false;
  if (
    w.athlete_id &&
    rider.athlete_id &&
    String(w.athlete_id) === String(rider.athlete_id)
  )
    return true;
  if (w.bib && rider.bib && String(w.bib) === String(rider.bib)) return true;
  if (
    w.name &&
    rider.name &&
    String(w.name).toLowerCase() === String(rider.name).toLowerCase()
  )
    return true;
  return false;
}
function findWithdrawal(withdrawals, rider, category) {
  return (withdrawals || []).find(
    (w) =>
      String(w.category || "Open") === String(category || "Open") &&
      withdrawalMatch(w, rider),
  );
}
async function loadSeedPreview(withdrawals) {
  if (!window.BracketProjection?.buildSeedsFromLiveEvents) return null;
  const feed = await publicGet("/api/public/events");
  const ids = (feed.events || [])
    .map((e) => String(e.id))
    .filter((id) => !id.startsWith("manual:") && !id.startsWith("preview:"));
  const resultMap = ids.length
    ? await publicGet(
        "/api/public/results?ids=" + encodeURIComponent(ids.join(",")),
      )
    : {};
  const items = (feed.events || []).map((e) => ({
    e,
    r: resultMap[String(e.id)] || [],
  }));
  const raw = BracketProjection.buildSeedsFromLiveEvents(items);
  if (!raw) return { before: null, after: null, items };
  const after = BracketProjection.applySeedWithdrawals(raw, withdrawals || []);
  return { before: raw, after, items };
}
/** Seeding-race riders for a category, including DNF / no-time (not in seed catalog). */
function seedingPoolRiders(items, category) {
  const cat = category || "Open";
  const byKey = new Map();
  for (const item of items || []) {
    const e = item.e || item;
    if (!BracketProjection?.isSeedingEvent?.(e)) continue;
    const eventCat = (() => {
      const blob = [e.tournament, e.name, e.stage, e.level]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      if (blob.includes("wild") && blob.includes("grom")) return "Wild Groms";
      if (blob.includes("wild") && blob.includes("women")) return "Wild Women";
      if (blob.includes("wild") && (/\bopen\b/.test(blob) || /\bmen/.test(blob)))
        return "Wild Open";
      if (blob.includes("grom")) return "Groms";
      if (blob.includes("women")) return "Women";
      if (/\bopen\b/.test(blob) || /\bmen/.test(blob)) return "Open";
      return null;
    })();
    if (eventCat !== cat) continue;
    for (const r of item.r || []) {
      const key = String(r.athlete_id || r.bib || r.name || "");
      if (!key || byKey.has(key)) continue;
      byKey.set(key, {
        athlete_id: r.athlete_id || "",
        bib: r.bib != null ? String(r.bib) : "",
        name: r.name || "",
        time: r.time || "",
        dnf: /dnf/i.test(String(r.time || "")),
      });
    }
  }
  return [...byKey.values()];
}
function riderRowHtml(rider, { out, seedLabel, extra }) {
  const state = out
    ? `<span class="out-badge">OUT</span>`
    : `<span class="in-badge">IN</span>`;
  const action = out
    ? `<button type="button" class="btn out-toggle" data-action="remove" data-id="${esc(out.id)}" data-bib="${esc(rider.bib || "")}" data-name="${esc(rider.name || "")}" data-athlete="${esc(rider.athlete_id || "")}">Undo · put back</button>`
    : `<button type="button" class="btn yellow out-toggle" data-action="add" data-bib="${esc(rider.bib || "")}" data-name="${esc(rider.name || "")}" data-athlete="${esc(rider.athlete_id || "")}" data-seed="${esc(rider.seed ?? "")}">Mark out</button>`;
  return `<li class="seed-toggle-row ${out ? "is-out" : ""}" data-bib="${esc(rider.bib || "")}" data-name="${esc((rider.name || "").toLowerCase())}" data-athlete="${esc(rider.athlete_id || "")}">
    <span class="seed-num">${esc(seedLabel)}</span>
    <span class="seed-bib">${rider.bib ? esc(rider.bib) : "—"}</span>
    <span class="seed-rider">${esc(rider.name || "Unknown")}${extra ? ` <span class="muted">${esc(extra)}</span>` : ""} ${state}</span>
    ${action}
  </li>`;
}
function wireSeedToggleButtons(root, category, status) {
  root.querySelectorAll(".out-toggle").forEach((btn) => {
    btn.onclick = async () => {
      btn.disabled = true;
      status.textContent = "Saving…";
      try {
        let d;
        if (btn.dataset.action === "remove") {
          d = await req("/api/admin/seed-withdrawals", {
            action: "remove",
            id: btn.dataset.id,
            bib: btn.dataset.bib,
            category,
          });
          status.textContent = "Restored to seed list.";
        } else {
          d = await req("/api/admin/seed-withdrawals", {
            action: "add",
            category,
            bib: btn.dataset.bib || "",
            name: btn.dataset.name || "",
            athleteId: btn.dataset.athlete || "",
            seed: btn.dataset.seed || null,
          });
          status.textContent = `Marked out: ${d.withdrawal?.name || d.withdrawal?.bib || "rider"}. Seeds bumped up.`;
        }
        await paintSeedOuts(d.withdrawals);
      } catch (e) {
        status.textContent = e.message;
        btn.disabled = false;
      }
    };
  });
}
function renderSeedOuts(withdrawals) {
  const preview = $("#out-preview"),
    count = $("#out-seed-count"),
    status = $("#out-status");
  if (!preview) return;
  const rows = withdrawals || [];
  const cat = $("#out-category")?.value || "Open";
  loadSeedPreview(rows)
    .then((view) => {
      if (!view?.before) {
        preview.innerHTML =
          '<div class="empty-tree">No seeding results to preview yet.</div>';
        count.textContent = "";
        return;
      }
      const before = view.before.categories?.[cat] || [];
      const after = view.after?.categories?.[cat] || [];
      const pool = seedingPoolRiders(view.items, cat);
      const seededIds = new Set(
        before.map((r) => String(r.athlete_id || "")).filter(Boolean),
      );
      const seededBibs = new Set(
        before.map((r) => String(r.bib || "")).filter(Boolean),
      );
      const orphans = pool.filter((r) => {
        if (r.athlete_id && seededIds.has(String(r.athlete_id))) return false;
        if (r.bib && seededBibs.has(String(r.bib))) return false;
        return true;
      });
      // Withdrawals that don't match anyone still listed (cleanup)
      const matched = new Set();
      const seedRows = before
        .slice()
        .sort((a, b) => (a.seed || 0) - (b.seed || 0))
        .map((r) => {
          const out = findWithdrawal(rows, r, cat);
          if (out) matched.add(out.id);
          return riderRowHtml(r, {
            out,
            seedLabel: r.seed != null ? `s${r.seed}` : "—",
            extra: "",
          });
        });
      const orphanRows = orphans.map((r) => {
        const out = findWithdrawal(rows, r, cat);
        if (out) matched.add(out.id);
        return riderRowHtml(
          { ...r, seed: null },
          {
            out,
            seedLabel: "—",
            extra: r.dnf ? "DNF / not seeded" : "not seeded",
          },
        );
      });
      const stray = rows.filter(
        (w) =>
          String(w.category || "Open") === cat && !matched.has(w.id),
      );
      const strayRows = stray.map((w) =>
        riderRowHtml(
          {
            athlete_id: w.athlete_id || "",
            bib: w.bib || "",
            name: w.name || "Unknown",
            seed: w.seed,
          },
          {
            out: w,
            seedLabel: w.seed != null ? `was s${w.seed}` : "—",
            extra: "orphan out (not in results)",
          },
        ),
      );
      const outCount = rows.filter(
        (w) => String(w.category || "Open") === cat,
      ).length;
      count.textContent = `· ${after.length} racing after bump · ${outCount} out · ${before.length} from seeding`;
      preview.innerHTML = `
        <p class="muted seed-outs-legend">Original seeding order below. <strong>OUT</strong> riders are removed and everyone below bumps up on the live board.</p>
        <ol class="seed-preview-list seed-toggle-list">${seedRows.join("") || '<li class="empty-tree">No seeds for this category.</li>'}</ol>
        ${orphanRows.length ? `<h4 class="seed-outs-sub">In seeding races but not in seed list</h4><ol class="seed-preview-list seed-toggle-list">${orphanRows.join("")}</ol>` : ""}
        ${strayRows.length ? `<h4 class="seed-outs-sub">Other outs (cleanup)</h4><ol class="seed-preview-list seed-toggle-list">${strayRows.join("")}</ol>` : ""}
        <p class="muted">Live seed list after bump: ${after.length} riders. Last slot (${cat === "Open" ? 32 : 16}) free for a manual fill if needed.</p>`;
      wireSeedToggleButtons(preview, cat, status);
    })
    .catch((e) => {
      preview.innerHTML = `<div class="empty-tree">${esc(e.message)}</div>`;
      count.textContent = "";
    });
}
async function paintSeedOuts(withdrawals) {
  const rows =
    withdrawals || (await req("/api/admin/seed-withdrawals")).withdrawals || [];
  renderSeedOuts(rows);
  return rows;
}
function wireSeedOuts() {
  const find = $("#out-find"),
    clear = $("#out-clear"),
    query = $("#out-query"),
    cat = $("#out-category"),
    status = $("#out-status"),
    preview = $("#out-preview");
  if (!preview || preview.dataset.wired) return;
  preview.dataset.wired = "1";
  cat?.addEventListener("change", () => paintSeedOuts());
  find &&
    (find.onclick = () => {
      const q = (query?.value || "").trim().toLowerCase();
      if (!q) {
        status.textContent = "Type a chip number or name, then Find.";
        return;
      }
      const rows = [
        ...(preview.querySelectorAll(".seed-toggle-row") || []),
      ];
      const hit = rows.find((row) => {
        const bib = (row.dataset.bib || "").toLowerCase();
        const name = (row.dataset.name || "").toLowerCase();
        return bib === q || name.includes(q) || bib.includes(q);
      });
      if (!hit) {
        status.textContent =
          "Not in this list — try the DNF section after refresh, or check category.";
        return;
      }
      rows.forEach((r) => r.classList.remove("seed-flash"));
      hit.classList.add("seed-flash");
      hit.scrollIntoView({ block: "center", behavior: "smooth" });
      status.textContent = hit.classList.contains("is-out")
        ? "Found (currently OUT)."
        : "Found — use Mark out on that row.";
    });
  clear &&
    (clear.onclick = async () => {
      if (!confirm("Clear all out-of-race marks for every category?")) return;
      try {
        const d = await req("/api/admin/seed-withdrawals", { action: "clear" });
        status.textContent = "Cleared all outs.";
        await paintSeedOuts(d.withdrawals);
      } catch (e) {
        status.textContent = e.message;
      }
    });
  query?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      find?.click();
    }
  });
}
const controls = (kind, id, name, extra = "") =>
  `<div class="tree-actions">${extra}<button class="icon move" data-kind="${kind}" data-id="${id}" data-dir="up" title="Move up">↑</button><button class="icon move" data-kind="${kind}" data-id="${id}" data-dir="down" title="Move down">↓</button><input class="rename" value="${esc(name)}"><button class="icon save-name" data-kind="${kind}" data-id="${id}" title="Save name">✓</button><button class="icon danger remove" data-kind="${kind}" data-id="${id}" title="Remove">×</button></div>`;
const division = (v) =>
  /women?/i.test(v)
    ? "women"
    : /grom/i.test(v)
      ? "groms"
      : /(men|open)/i.test(v)
        ? "open"
        : "";
const isWild = (v) => /\bwild(?:\s*card)?\b/i.test(v);
const normal = (v) =>
  String(v)
    .toLowerCase()
    .replace(/wild\s*card/g, "wild")
    .replace(/heats?/g, "heat")
    .replace(/quarters?/g, "quarter")
    .replace(/semis?/g, "semi")
    .replace(/wild|women'?s?|men'?s?|groms?|open|qualifiers?|finals?/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
const QF_FROM_HEATS = [
    [
      [1, 0],
      [5, 1],
      [8, 0],
      [4, 1],
    ],
    [
      [2, 0],
      [6, 1],
      [7, 0],
      [3, 1],
    ],
    [
      [6, 0],
      [2, 1],
      [3, 0],
      [7, 1],
    ],
    [
      [5, 0],
      [1, 1],
      [4, 0],
      [8, 1],
    ],
  ],
  SF_FROM_QUARTERS = [
    [
      [1, 0],
      [3, 1],
      [4, 0],
      [2, 1],
    ],
    [
      [2, 0],
      [4, 1],
      [3, 0],
      [1, 1],
    ],
  ],
  FINAL_FROM_SEMIS = [
    [1, 0],
    [2, 0],
    [1, 1],
    [2, 1],
  ];
function bracketKey(e) {
  const text = [e.tournament, e.stage, e.name]
      .filter(Boolean)
      .join(" ")
      .toLowerCase(),
    family = isWild(text) ? "wild-" + division(text) : division(text);
  if (!family) return null;
  let m = text.match(/quarter\s*(\d+)/);
  if (m) return { family, stage: "quarter", num: +m[1] };
  m = text.match(/heat\s*(\d+)/);
  if (m) return { family, stage: "heat", num: +m[1] };
  m = text.match(/semi\s*(\d+)/);
  if (m) return { family, stage: "semi", num: +m[1] };
  if (/runner\s*up/.test(text)) return { family, stage: "runnerup", num: 1 };
  if (/3rd/.test(text)) return { family, stage: "third", num: 1 };
  if (/4th/.test(text)) return { family, stage: "fourth", num: 1 };
  if (/\bfinal\b/.test(text) && !/semi/.test(text))
    return { family, stage: "final", num: 1 };
  return null;
}
function feederSlots(key) {
  if (!key) return [];
  if (key.stage === "quarter")
    return (QF_FROM_HEATS[key.num - 1] || []).map(([num, place]) => ({
      stage: "heat",
      num,
      place,
    }));
  if (key.stage === "semi")
    return (SF_FROM_QUARTERS[key.num - 1] || []).map(([num, place]) => ({
      stage: "quarter",
      num,
      place,
    }));
  if (key.stage === "final")
    return FINAL_FROM_SEMIS.map(([num, place]) => ({
      stage: "semi",
      num,
      place,
    }));
  if (!key.family.startsWith("wild-") && key.stage === "third")
    return [1, 2, 3, 4].map((num) => ({ stage: "quarter", num, place: 2 }));
  if (!key.family.startsWith("wild-") && key.stage === "fourth")
    return [1, 2, 3, 4].map((num) => ({ stage: "quarter", num, place: 3 }));
  if (!key.family.startsWith("wild-") && key.stage === "runnerup")
    return [
      { stage: "semi", num: 1, place: 2 },
      { stage: "semi", num: 1, place: 3 },
      { stage: "semi", num: 2, place: 2 },
      { stage: "semi", num: 2, place: 3 },
    ];
  return [];
}
function qualifiedBibs(target, events, results) {
  const key = bracketKey(target),
    slots = feederSlots(key);
  if (!slots.length) return "";
  const source = new Map(
    events.map((e) => {
      const k = bracketKey(e);
      return [k ? keyString(k) : "", e];
    }),
  );
  const bibs = slots.map((slot) => {
    const e = source.get(
      keyString({ family: key.family, stage: slot.stage, num: slot.num }),
    );
    const rows = (results?.[e?.id] || [])
      .filter((x) => Number(x.position) > 0)
      .sort((a, b) => Number(a.position) - Number(b.position));
    return rows[slot.place]?.bib || "";
  });
  return bibs.every(Boolean) ? bibs.join(",") : "";
}
function keyString(k) {
  return k.family + ":" + k.stage + ":" + k.num;
}
function feedHealth(d) {
  const box = $("#feed-health");
  if (!box) return;
  const paused = d.meta.feed_paused === "true",
    last = d.meta.last_feed_check ? new Date(d.meta.last_feed_check) : null,
    imported = d.meta.last_import ? new Date(d.meta.last_import) : null;
  const age = last ? Math.max(0, Math.floor((Date.now() - last) / 1000)) : null,
    remaining =
      age === null ? "—" : Math.max(0, Number(d.pollSeconds || 30) - age);
  const stamp = (date) =>
    date
      ? date.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        })
      : "Waiting";
  box.className =
    "feed-health " + (paused ? "paused" : d.meta.last_error ? "error" : "ok");
  box.innerHTML = `<strong>${paused ? "Reader paused" : "RDF reader active"}</strong><span>${paused ? "Resume live updates to read the export." : `Last checked ${stamp(last)} · next check in ${remaining}s`}</span><small>Last import: ${stamp(imported)} · ${d.file.exists ? `${d.file.bytes.toLocaleString()} bytes` : "Source file not found"}</small>`;
}
function branch(title, body, actions = "", key = "") {
  return `<section class="tree-node" data-node="${key}"><div class="tree-line"><button class="tree-toggle">∨</button><strong>${title}</strong>${actions}</div><div class="tree-children" ${collapsed.has(key) ? "hidden" : ""}>${body}</div></section>`;
}
async function render() {
  try {
    const d = await req("/api/admin/status"),
      paused = d.meta.feed_paused === "true",
      sourceMode = d.meta.source_mode === "paste" ? "paste" : "rdf";
    $("#source-mode").value = sourceMode;
    $("#paste-source").hidden = sourceMode !== "paste";
    if (!initialized) {
      d.levels.forEach((x) => collapsed.add("level-" + x.id));
      initialized = true;
    }
    $("#app-version").textContent = "Version " + d.version;
    $("#export-dir").value = "RACE_EXPORT_DIR=" + d.sourceConfig.hostDirectory;
    $("#export-filename").value =
      "RACE_EXPORT_FILENAME=" + d.sourceConfig.filename;
    $("#file-status").textContent = d.file.exists
      ? `Reading ${d.file.configured} · ${d.file.bytes} bytes`
      : `Waiting for ${d.file.configured}`;
    $("#import-status").textContent = d.meta.last_error
      ? "Last error: " + d.meta.last_error
      : "Last successful import: " + (d.meta.last_import || "waiting");
    $("#event-status").value = d.meta.status || d.status || "Live";
    feedHealth(d);
    $("#feed-toggle").textContent = paused
      ? "Start live updates"
      : "Stop live updates";
    let html = `<div class="tree-create"><input id="new-tournament" placeholder="New tournament"><button class="btn create-tournament">Add tournament</button></div>`;
    html += d.tournaments
      .map((t) => {
        const levels = d.levels.filter((l) => l.tournament_id === t.id);
        const levelHtml = levels
          .map((l) => {
            const races = d.races.filter((r) => r.level_id === l.id);
            const raceHtml = races
              .map((r) => {
                const events = d.events.filter(
                  (e) =>
                    e.tournament === t.name &&
                    e.level === l.name &&
                    e.stage === r.name,
                );
                const assigned = events.length
                  ? events
                      .map((e) => {
                        const bibs = qualifiedBibs(e, d.events, d.eventResults);
                        return `<span class="assigned-race"><span>${esc(e.name)}</span>${bibs ? ` <span class="assigned-bibs"><label>Ready to paste</label><input class="race-bibs" value="${esc(bibs)}" readonly aria-label="Qualified race numbers for ${esc(e.name)}"><button class="icon copy-bibs" type="button" data-bibs="${esc(bibs)}" title="Copy qualified race numbers">⧉</button></span>` : ""}${r.fastest_lap ? '<span class="race-mode-tag" title="This race displays each rider’s fastest lap">↻ Multi-lap</span>' : ""} <select class="publish-mode" data-id="${esc(e.id)}"><option value="populated" ${(e.publish_mode || "populated") === "populated" ? "selected" : ""}>Show when populated</option><option value="always" ${e.publish_mode === "always" ? "selected" : ""}>Always show</option><option value="hide" ${e.publish_mode === "hide" ? "selected" : ""}>Hide</option></select></span>`;
                      })
                      .join('<span class="assigned-separator"> · </span>')
                  : '<span class="unassigned-label">No RaceTec race assigned</span>';
                return `<div class="assignment-row"><strong>${esc(r.name)}</strong><span class="assignment-tilde">~</span>${assigned}${controls("races", r.id, r.name, `<button class="visibility lap-toggle" data-r="${r.id}">${r.fastest_lap ? "Multi lap: True" : "Multi lap: False"}</button>`)}</div>`;
              })
              .join("");
            return branch(
              esc(l.name),
              raceHtml +
                `<div class="tree-create compact"><input placeholder="New race"><button class="btn create-race" data-t="${t.id}" data-l="${l.id}">Add race</button></div>`,
              controls("levels", l.id, l.name),
              `level-${l.id}`,
            );
          })
          .join("");
        return branch(
          esc(t.name),
          levelHtml +
            `<div class="tree-create compact"><input placeholder="New round"><button class="btn create-level" data-t="${t.id}">Add round</button></div>`,
          controls(
            "tournaments",
            t.id,
            t.name,
            `<label class="highlight-control">Highlight top <select class="highlight-count" data-t="${t.id}" aria-label="Highlighted riders for ${esc(t.name)}">${[0, 1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${Number(t.highlight_count ?? 2) === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>`,
          ),
          `tournament-${t.id}`,
        );
      })
      .join("");
    const unassigned = d.events.filter(
      (e) => !d.tournaments.some((t) => t.name === e.tournament) && e.name,
    );
    html += branch(
      "Unassigned RaceTec races",
      unassigned
        .map(
          (e) =>
            `<div class="unassigned-race"><span>${esc(e.name)}</span><select data-event="${esc(e.id)}"><option value="">Choose destination…</option>${d.races
              .map((r) => {
                const l = d.levels.find((x) => x.id === r.level_id),
                  t = d.tournaments.find((x) => x.id === r.tournament_id);
                return l && t
                  ? `<option value="${r.id}">${esc(t.name)} → ${esc(l.name)} → ${esc(r.name)}</option>`
                  : "";
              })
              .join(
                "",
              )}</select><button class="btn assign" data-event="${esc(e.id)}">Assign</button></div>`,
        )
        .join("") || '<div class="empty-tree">Everything is assigned.</div>',
      '<button class="btn auto-assign">Auto-assign matches</button>',
      "unassigned",
    );
    $("#event-list").innerHTML =
      `<div class="tree-toolbar"><span>Open a section to inspect it. Use Edit structure to change the setup.</span><div class="tree-toolbar-actions"><button class="btn" id="seed-setup">Set up seeding</button><button class="btn" id="tree-edit">${editMode ? "Done editing" : "Edit structure"}</button></div></div>` +
      html;
    $("#event-list").classList.toggle("editing", editMode);
    wire(d);
    wireSeedOuts();
    paintSeedOuts(d.seedWithdrawals || []);
  } catch (e) {
    $("#event-list").textContent = e.message;
  }
}
function wire(d) {
  const forced = d.meta.force_show_all === "true",
    sourceMode = d.meta.source_mode === "paste" ? "paste" : "rdf";
  $("#source-mode").onchange = async (e) => {
    await req("/api/admin/source-mode", { mode: e.target.value });
    render();
  };
  $("#import-pasted").onclick = async () => {
    const text = $("#pasted-results").value.trim(),
      replace = $("#replace-pasted")?.checked,
      button = $("#import-pasted"),
      status = $("#paste-status");
    if (!text) {
      status.textContent = "Paste a RaceTec table first.";
      return;
    }
    button.disabled = true;
    button.textContent = "Importing…";
    try {
      const result = await req("/api/admin/import-text", { text, replace });
      status.textContent = `Imported ${result.riders} riders across ${result.events} event${result.events === 1 ? "" : "s"}.`;
      $("#pasted-results").value = "";
      await render();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      const nextButton = $("#import-pasted");
      if (nextButton) {
        nextButton.disabled = false;
        nextButton.textContent = "Import pasted results";
      }
    }
  };
  const chipReturn = d.meta.chip_return_mode === "true";
  const chipButton = $("#chip-return-toggle");
  if (chipButton) {
    chipButton.textContent = chipReturn
      ? "Chip return view: On"
      : "Chip return view: Off";
    chipButton.classList.toggle("yellow", chipReturn);
    chipButton.onclick = async () => {
      await req("/api/admin/chip-return-mode", { enabled: !chipReturn });
      render();
    };
  }
  $("#force-show-all").textContent = forced
    ? "Show all races: On"
    : "Individual settings: Off";
  $("#force-show-all").onclick = async () => {
    await req("/api/admin/show-empty", { enabled: !forced });
    render();
  };
  $("#event-list")
    .querySelectorAll(".publish-mode")
    .forEach(
      (x) =>
        (x.onchange = async () => {
          await req("/api/admin/events/" + encodeURIComponent(x.dataset.id), {
            mode: x.value,
          });
          render();
        }),
    );
  $("#event-list")
    .querySelectorAll(".copy-bibs")
    .forEach(
      (button) =>
        (button.onclick = async () => {
          const value = button.dataset.bibs || "";
          try {
            await navigator.clipboard.writeText(value);
          } catch (_) {
            const input = button.parentElement.querySelector(".race-bibs");
            input.focus();
            input.select();
            document.execCommand("copy");
          }
          const original = button.textContent;
          button.textContent = "✓";
          setTimeout(() => (button.textContent = original), 900);
        }),
    );
  $("#event-list")
    .querySelectorAll(".highlight-count")
    .forEach(
      (x) =>
        (x.onchange = async () => {
          await req(
            "/api/admin/tournaments/" + x.dataset.t + "/highlight-count",
            { count: +x.value },
          );
          render();
        }),
    );
  $(".auto-assign")?.addEventListener("click", async () => {
    const unassigned = d.events.filter(
      (e) => !d.tournaments.some((t) => t.name === e.tournament) && e.name,
    );
    const assignments = [];
    for (const event of unassigned) {
      const wanted = normal(event.name),
        group = division(event.name),
        matches = d.races.filter((r) => {
          const level = d.levels.find((x) => x.id === r.level_id),
            tournament = d.tournaments.find((x) => x.id === r.tournament_id),
            isSeeding = /seed|qualif|\bq\s*[12]\b|\btt\b|time\s*trial/i.test(
              event.name,
            ),
            eventQ = (event.name.match(/\bq\s*([12])\b/i) || [])[1],
            raceQ = (r.name.match(/\bq\s*([12])\b/i) || [])[1],
            matchesSeeding = normal(r.name) === "seeding" && isSeeding,
            matchesRound = eventQ && raceQ && eventQ === raceQ && isSeeding;
          return (
            level &&
            tournament &&
            (normal(r.name) === wanted || matchesSeeding || matchesRound) &&
            isWild(event.name) === isWild(tournament.name) &&
            (!group || division(tournament.name) === group)
          );
        });
      if (matches.length === 1) {
        const race = matches[0],
          level = d.levels.find((x) => x.id === race.level_id),
          tournament = d.tournaments.find((x) => x.id === race.tournament_id);
        assignments.push({
          eventId: event.id,
          tournament: tournament.name,
          level: level.name,
          race: race.name,
          raceId: race.id,
          name: event.name,
        });
      }
    }
    if (!assignments.length) {
      alert(
        "No unambiguous matches found. Unmatched races remain available for manual assignment.",
      );
      return;
    }
    await req("/api/admin/assignments", { assignments });
    render();
  });
  $("#force-read").onclick = async () => {
    const b = $("#force-read");
    b.disabled = true;
    b.textContent = "Reading RDF…";
    try {
      await req("/api/admin/import-now", {});
      await render();
    } catch (err) {
      alert(err.message);
      b.disabled = false;
      b.textContent = "Force read RDF";
    }
  };
  $("#seed-setup")?.addEventListener("click", async () => {
    const result = await req("/api/admin/seed-setup", {});
    alert(
      result.created
        ? `Created ${result.created} seeding race${result.created === 1 ? "" : "s"}.`
        : "All seeding races are already set up.",
    );
    render();
  });
  $("#tree-edit").onclick = () => {
    editMode = !editMode;
    render();
  };
  $("#save-setup").onclick = async () => {
    await req("/api/admin/save", {});
    $("#save-setup").textContent = "Saved";
    setTimeout(() => ($("#save-setup").textContent = "Save changes"), 1200);
  };
  $("#feed-toggle").onclick = async () => {
    await req("/api/admin/feed", { running: d.meta.feed_paused === "true" });
    render();
  };
  $("#event-status").onchange = async (e) => {
    await req("/api/admin/status", { status: e.target.value });
    render();
  };
  $("#event-list").onclick = async (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    try {
      if (b.classList.contains("tree-toggle")) {
        const c = b
          .closest(".tree-node")
          .querySelector(":scope > .tree-children");
        c.hidden = !c.hidden;
        const key = b.closest(".tree-node").dataset.node;
        if (key) {
          c.hidden ? collapsed.add(key) : collapsed.delete(key);
        }
        b.textContent = c.hidden ? "›" : "∨";
      } else if (b.classList.contains("move")) {
        await req(
          "/api/admin/" + b.dataset.kind + "/" + b.dataset.id + "/move",
          { direction: b.dataset.dir },
        );
        render();
      } else if (b.classList.contains("save-name")) {
        const input = b.parentElement.querySelector(".rename"),
          name = input.value.trim();
        if (name) {
          await req("/api/admin/" + b.dataset.kind + "/" + b.dataset.id, {
            name,
          });
          render();
        }
      } else if (b.classList.contains("remove")) {
        if (confirm("Remove this item and unassign its races?")) {
          await req("/api/admin/" + b.dataset.kind + "/" + b.dataset.id, {
            delete: true,
          });
          render();
        }
      } else if (b.classList.contains("create-tournament")) {
        const name = $("#new-tournament").value.trim();
        if (name) {
          await req("/api/admin/tournaments", { name });
          render();
        }
      } else if (b.classList.contains("create-level")) {
        const name = b.parentElement.querySelector("input").value.trim();
        if (name) {
          await req("/api/admin/levels", { tournamentId: +b.dataset.t, name });
          render();
        }
      } else if (b.classList.contains("create-race")) {
        const name = b.parentElement.querySelector("input").value.trim();
        if (name) {
          await req("/api/admin/races", {
            tournamentId: +b.dataset.t,
            levelId: +b.dataset.l,
            name,
          });
          render();
        }
      } else if (b.classList.contains("lap-toggle")) {
        const r = d.races.find((x) => x.id === +b.dataset.r);
        await req("/api/admin/races/" + r.id + "/fastest-lap", {
          enabled: !r.fastest_lap,
        });
        render();
      } else if (b.classList.contains("assign")) {
        const row = b.closest(".unassigned-race"),
          race = d.races.find(
            (x) => x.id === +row.querySelector("select").value,
          ),
          event = d.events.find((x) => x.id === b.dataset.event);
        if (!race || !event) return;
        const level = d.levels.find((x) => x.id === race.level_id),
          tournament = d.tournaments.find((x) => x.id === race.tournament_id);
        await req("/api/admin/assignments", {
          assignments: [
            {
              eventId: event.id,
              tournament: tournament.name,
              level: level.name,
              race: race.name,
              raceId: race.id,
              name: event.name,
            },
          ],
        });
        render();
      } else if (b.classList.contains("visibility")) {
        await req("/api/admin/events/" + encodeURIComponent(b.dataset.id), {
          visible: b.textContent === "Hidden",
        });
        render();
      }
    } catch (err) {
      alert(err.message);
    }
  };
}
render();
setInterval(async () => {
  try {
    feedHealth(await req("/api/admin/status"));
  } catch (_) {
    const box = $("#feed-health");
    if (box) {
      box.className = "feed-health error";
      box.textContent = "Unable to check RDF reader status.";
    }
  }
}, 5000);
