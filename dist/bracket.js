/** EOL / OWAR knockout feed + staggered start gates. Frontend-only. */
(function (global) {
  const ROLE = { W: 0, RU: 1, "3rd": 2, "4th": 3 };

  /** Official EOL 32-rider poster heat packs (gate order = array order). */
  const EOL_EIGHTH_FINALS_32 = [
    [1, 16, 17, 25],
    [2, 15, 18, 26],
    [3, 14, 19, 27],
    [4, 13, 20, 28],
    [5, 12, 21, 29],
    [6, 11, 22, 30],
    [7, 10, 23, 31],
    [8, 9, 24, 32],
  ];

  /**
   * Quarter slots from heats — poster mix so a heat's W and RU never share a QF.
   * Each entry: [heatNum 1-based, place 0=W / 1=RU]
   */
  const QF_FROM_HEATS = [
    [
      [1, 0],
      [5, 1],
      [8, 0],
      [4, 1],
    ], // QF1
    [
      [2, 0],
      [6, 1],
      [7, 0],
      [3, 1],
    ], // QF2
    [
      [6, 0],
      [2, 1],
      [3, 0],
      [7, 1],
    ], // QF3
    [
      [5, 0],
      [1, 1],
      [4, 0],
      [8, 1],
    ], // QF4
  ];

  const SF_FROM_QUARTERS = [
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
  ];

  const FINAL_FROM_SEMIS = [
    [1, 0],
    [2, 0],
    [1, 1],
    [2, 1],
  ];

  const FAMILY_SEED_CAT = {
    "open-men": "Open",
    women: "Women",
    groms: "Groms",
    "wild-men": "Wild Open",
    "wild-women": "Wild Women",
    "wild-groms": "Wild Groms",
  };

  let seedCatalog = null; // { categories: { Open: [{name,seed,timeSec}] } }
  let seedIndex = new Map(); // normName -> {seed,timeSec,name,category}

  function normName(value) {
    return String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/['’`]/g, "")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function familyFromName(name) {
    const n = String(name || "").toLowerCase();
    if (n.includes("wild") && n.includes("women")) return "wild-women";
    if (n.includes("wild") && n.includes("grom")) return "wild-groms";
    if (n.includes("wild")) return "wild-men";
    if (n.includes("grom")) return "groms";
    if (n.includes("women")) return "women";
    if (/\bmen/.test(n) || n.includes("open")) return "open-men";
    return null;
  }

  function parseHeat(name) {
    const n = String(name || "")
      .toLowerCase()
      .replace(/['’]/g, "");
    if (/32nd|placment|placement|regist|infinity|surf|legend|chair/.test(n)) {
      return null;
    }
    const family = familyFromName(n);
    if (!family) return null;
    if (/runner\s*ups?/.test(n)) return { family, stage: "runnerup", num: 1, key: `${family}:runnerup:1` };
    if (/3rd[s]?/.test(n)) return { family, stage: "third", num: 1, key: `${family}:third:1` };
    if (/4th[s]?/.test(n)) return { family, stage: "fourth", num: 1, key: `${family}:fourth:1` };
    let m = n.match(/heat[s]?\s*(\d+)/);
    if (m) return { family, stage: "heat", num: +m[1], key: `${family}:heat:${m[1]}` };
    m = n.match(/quarter[s]?\s*(\d+)/);
    if (m) return { family, stage: "quarter", num: +m[1], key: `${family}:quarter:${m[1]}` };
    m = n.match(/semi[s]?\s*(\d+)/);
    if (m) return { family, stage: "semi", num: +m[1], key: `${family}:semi:${m[1]}` };
    if (/\bfinals?\b/.test(n) && !/semi/.test(n)) {
      return { family, stage: "final", num: 1, key: `${family}:final:1` };
    }
    return null;
  }

  function displayName(family, stage, num) {
    const prefix = {
      "open-men": "Open",
      women: "Womens",
      groms: "Groms",
      "wild-men": "Wild - Mens",
      "wild-women": "Wild - Womens",
      "wild-groms": "Wild - Groms",
    }[family] || "Race";
    if (stage === "heat") return `${prefix} Heats ${num}`;
    if (stage === "quarter") return `${prefix} Quarter ${num}`;
    if (stage === "semi") return `${prefix} Semi ${num}`;
    if (stage === "third") return `${prefix} 3rd\'s`;
    if (stage === "fourth") return `${prefix} 4th\'s`;
    if (stage === "runnerup") return `${prefix} Runner Up\'s`;
    return `${prefix} Final`;
  }

  function navigationStage(stage) {
    return { heat: "Heats", quarter: "Quarters", semi: "Semi", third: "Semi", fourth: "Semi", final: "Finals", runnerup: "Finals" }[stage];
  }

  function roleLabel(stage, num, role) {
    const place =
      role === "W" ? "Winner" : role === "RU" ? "2nd place" : role === "3rd" ? "3rd place" : "4th place";
    const round =
      stage === "heat"
        ? `H${num}`
        : stage === "quarter"
          ? `QF${num}`
          : stage === "semi"
            ? `SF${num}`
            : "Final";
    return `${place} of ${round}`;
  }

  function riderHasTime(rider) {
    if (!rider) return false;
    if (rider.timeSec != null && Number.isFinite(Number(rider.timeSec))) return true;
    return Boolean(String(rider.time || "").trim());
  }

  function categoryHasTimes(category) {
    return (seedCatalog?.categories?.[category] || []).some(riderHasTime);
  }

  function slotsFromHeatResults(spec) {
    return spec.map(([h, place]) => ({
      from: { stage: "heat", num: h },
      role: place === 0 ? "W" : "RU",
    }));
  }

  function slotsFromQuarters(spec) {
    return spec.map(([q, place]) => ({
      from: { stage: "quarter", num: q },
      role: place === 0 ? "W" : "RU",
    }));
  }

  function slotsFromSemis() {
    return FINAL_FROM_SEMIS.map(([s, place]) => ({
      from: { stage: "semi", num: s },
      role: place === 0 ? "W" : "RU",
    }));
  }

  // The 3rd- and 4th-place races each take one rider from all four
  // quarter-finals, so each race has a full four-rider grid.
  function placementSlots(role) {
    return [1, 2, 3, 4].map((num) => ({ from: { stage: "quarter", num }, role }));
  }

  function runnerUpSlots() {
    return [
      { from: { stage: "semi", num: 1 }, role: "3rd" },
      { from: { stage: "semi", num: 1 }, role: "4th" },
      { from: { stage: "semi", num: 2 }, role: "3rd" },
      { from: { stage: "semi", num: 2 }, role: "4th" },
    ];
  }

  function feedSlots(stage, num) {
    if (stage === "quarter") {
      const spec = QF_FROM_HEATS[num - 1];
      return spec ? slotsFromHeatResults(spec) : [];
    }
    if (stage === "semi") {
      const spec = SF_FROM_QUARTERS[num - 1];
      return spec ? slotsFromQuarters(spec) : [];
    }
    if (stage === "third") return placementSlots("3rd");
    if (stage === "fourth") return placementSlots("4th");
    if (stage === "final") return slotsFromSemis();
    if (stage === "runnerup") return runnerUpSlots();
    return [];
  }

  function expectedNodes(family, seenStages) {
    // Build every downstream path as soon as a tournament has a configured
    // first round (or a seeding session). This makes the whole route visible
    // before RaceTec has published a result in any individual race.
    if (!seenStages.size) return [];

    const category = FAMILY_SEED_CAT[family];
    // Open runs a 32-rider heat round. Women and Groms are fixed 16-rider
    // tournaments, so their seed list starts directly at the four quarters.
    const riderCount = (seedCatalog?.categories?.[category] || []).length || (family === "open-men" ? 32 : 16);
    const hasHeats = family === "open-men" || riderCount > 16;
    const hasQuarters = hasHeats || riderCount > 8;
    const nodes = [];

    if (hasHeats) {
      for (let i = 1; i <= 8; i++) nodes.push({ stage: "heat", num: i });
    }
    if (hasQuarters) {
      for (let i = 1; i <= 4; i++) nodes.push({ stage: "quarter", num: i });
    }

    // Every tournament converges at two semis, then exposes the main final
    // and all consolation paths sourced from those same semis.
    nodes.push({ stage: "semi", num: 1 }, { stage: "semi", num: 2 });
    nodes.push({ stage: "third", num: 1 }, { stage: "fourth", num: 1 });
    nodes.push({ stage: "final", num: 1 }, { stage: "runnerup", num: 1 });

    return nodes;
  }

  function sortFinishers(rows) {
    return [...(rows || [])].sort(
      (a, b) =>
        (Number(a.position) || 999) - (Number(b.position) || 999) ||
        String(a.name || "").localeCompare(String(b.name || "")),
    );
  }

  function hasRecordedResult(row) {
    if (!row) return false;
    if (Number(row.position) > 0) return true;
    const t = String(row.time || "").trim();
    return Boolean(t) && t.toLowerCase() !== "not raced yet";
  }

  function recordedFinishers(rows) {
    return sortFinishers(rows).filter(hasRecordedResult);
  }

  /** First-round gate list from TT seed zigzag (Seed N until TT times exist). */
  function seedStartRows(family, stage, num) {
    const cat = FAMILY_SEED_CAT[family];
    if (!cat) return null;
    const board = buildCategoryHeatGrids(cat);
    const want =
      stage === "heat"
        ? "Heat"
        : stage === "quarter"
          ? "Quarter"
          : stage === "semi"
            ? "Semi"
            : null;
    if (!want || !board.heats.length) return null;
    const prefix = board.heats[0].title.replace(/\s+\d+$/, "");
    if (prefix !== want) return null;
    const heat = board.heats[num - 1];
    if (!heat) return null;
    return heat.slots.map((s) => ({
      position: null,
      athlete_id: "",
      name: s.known ? s.name : `Seed ${s.seed}`,
      bib: "—",
      time: "Not raced yet",
      pending: true,
      known: s.known,
      seed: s.seed,
    }));
  }

  function syntheticEvent(family, key, stage, num, items) {
    return {
      id: `projected:${key}`,
      name: displayName(family, stage, num),
      stage: displayName(family, stage, num),
      tournament:
        items.find((x) => familyFromName(x.e.tournament || x.e.name) === family)?.e?.tournament ||
        items[0]?.e?.tournament ||
        "",
      level: items[0]?.e?.level || "",
      highlight_count: 2,
      multi_lap: 0,
      count: 0,
    };
  }

  function lookupSeed(name, family) {
    const key = normName(name);
    if (!key) return null;
    const direct = seedIndex.get(key);
    if (direct) return direct;
    // last-name fallback within preferred category
    const parts = key.split(" ");
    const last = parts[parts.length - 1];
    if (last && last.length > 2) {
      const cat = FAMILY_SEED_CAT[family];
      const matches = [];
      for (const [k, v] of seedIndex) {
        if (!k.endsWith(" " + last) && k !== last) continue;
        if (cat && v.category !== cat && v.category !== "Open") continue;
        matches.push(v);
      }
      if (matches.length === 1) return matches[0];
    }
    return null;
  }

  function seedMeta(name, family) {
    const hit = lookupSeed(name, family);
    return {
      seed: hit?.seed ?? null,
      seedTimeSec: hit?.timeSec ?? null,
      seedTime: hit?.time ?? null,
    };
  }

  function compareSeed(a, b) {
    const as = a.seed == null ? 9999 : Number(a.seed);
    const bs = b.seed == null ? 9999 : Number(b.seed);
    if (as !== bs) return as - bs;
    const at = a.seedTimeSec == null ? 99999 : Number(a.seedTimeSec);
    const bt = b.seedTimeSec == null ? 99999 : Number(b.seedTimeSec);
    if (at !== bt) return at - bt;
    return String(a.name || "").localeCompare(String(b.name || ""));
  }

  /** Tag riders with W/RU from whichever prior-stage heat they actually advanced from. */
  function inferRoleFromPreviousStage(rider, family, stage, byKey) {
    const prev = { quarter: "heat", semi: "quarter", final: "semi" }[stage];
    if (!prev) return null;
    const id = rider.athlete_id != null ? String(rider.athlete_id) : "";
    const name = normName(rider.name);
    for (const [key, item] of byKey) {
      if (!key.startsWith(`${family}:${prev}:`)) continue;
      const finishers = sortFinishers(item.r);
      const idx = finishers.findIndex(
        (f) => (id && String(f.athlete_id) === id) || normName(f.name) === name,
      );
      if (idx === 0) return { fromRole: "W", fromLabel: `Winner · ${item.e?.name || prev}` };
      if (idx === 1) return { fromRole: "RU", fromLabel: `2nd · ${item.e?.name || prev}` };
    }
    return null;
  }

  function tagRolesFromFeeders(finishers, family, stage, num, byKey) {
    return finishers.map((r) => {
      const tag = inferRoleFromPreviousStage(r, family, stage, byKey) || {};
      return {
        ...r,
        finishPos: Number(r.position) || null,
        fromRole: tag.fromRole,
        fromLabel: tag.fromLabel,
        ...seedMeta(r.name, family),
      };
    });
  }

  /**
   * Staggered starts:
   * - First round (heats): gate order = better seed first among riders in the heat.
   * - Later rounds: both 1sts → gates 1–2 (by seed); both 2nds → gates 3–4 (by seed).
   */
  function assignStartPositions(rows, { firstRound }) {
    const list = rows.map((r, i) => ({ ...r, _i: i }));
    let ordered;
    if (firstRound) {
      ordered = [...list].sort(compareSeed);
    } else {
      const winners = list.filter((r) => r.fromRole === "W").sort(compareSeed);
      const seconds = list.filter((r) => r.fromRole === "RU").sort(compareSeed);
      const rest = list.filter((r) => r.fromRole !== "W" && r.fromRole !== "RU").sort(compareSeed);
      ordered =
        winners.length || seconds.length
          ? [...winners, ...seconds, ...rest]
          : [...list].sort(compareSeed);
    }
    return ordered.map((r, idx) => {
      const { _i, ...rest } = r;
      return { ...rest, startPos: idx + 1 };
    });
  }

  function decorateRows(rows, family, stage, num, byKey, pending) {
    const hasFeederRoles = rows.some((r) => r.fromRole);
    const firstRound = !hasFeederRoles;
    let enriched;
    if (pending) {
      enriched = rows.map((r) => {
        const meta = r.known ? seedMeta(r.name, family) : {};
        return {
          ...r,
          finishPos: null,
          fromRole: r.fromRole,
          seed: r.seed ?? meta.seed ?? null,
          seedTimeSec: meta.seedTimeSec ?? null,
          seedTime: meta.seedTime ?? null,
        };
      });
    } else {
      enriched = tagRolesFromFeeders(rows, family, stage, num, byKey);
    }
    return assignStartPositions(enriched, { firstRound });
  }

  function enrichTournament(items) {
    const byKey = new Map();
    const extras = [];
    const seenStagesByFamily = new Map();

    for (const item of items) {
      const parsed =
        parseHeat([item.e.tournament, item.e.stage].filter(Boolean).join(" ")) ||
        parseHeat([item.e.tournament, item.e.name].filter(Boolean).join(" "));
      if (!parsed) {
        extras.push({
          ...item,
          pending: false,
          r: sortFinishers(item.r).map((r) => ({
            ...r,
            finishPos: Number(r.position) || null,
            ...seedMeta(r.name, familyFromName(item.e.tournament || item.e.name) || "open-men"),
            startPos: null,
          })),
        });
        continue;
      }
      byKey.set(parsed.key, { ...item, parsed });
      if (!seenStagesByFamily.has(parsed.family)) seenStagesByFamily.set(parsed.family, new Set());
      seenStagesByFamily.get(parsed.family).add(parsed.stage);
    }

    // A seeding session is enough to draw the first knockout round, even before
    // RaceTec publishes an empty heat. This keeps the main board aligned with
    // the dedicated seeding board from the first qualifying lap onward.
    for (const item of items) {
      if (!isSeedingEvent(item.e)) continue;
      const family = familyFromName(item.e.tournament || item.e.name);
      const category = FAMILY_SEED_CAT[family];
      const count = (seedCatalog?.categories?.[category] || []).length;
      if (!family || !category || !count || seenStagesByFamily.has(family)) continue;
      const firstStage = family === "open-men" || count > 16 ? "heat" : count > 8 ? "quarter" : "semi";
      seenStagesByFamily.set(family, new Set([firstStage]));
    }

    const projected = [];
    for (const [family, seenStages] of seenStagesByFamily) {
      for (const node of expectedNodes(family, seenStages)) {
        const key = `${family}:${node.stage}:${node.num}`;
        const existing = byKey.get(key);
        const finishers = recordedFinishers(existing?.r);
        const raced = finishers.length > 0;

        if (raced && existing) {
          const rows = decorateRows(finishers, family, node.stage, node.num, byKey, false);
          projected.push({
            e: existing.e,
            r: rows,
            pending: false,
            parsed: { ...node, family, key },
          });
          continue;
        }

        const slots = feedSlots(node.stage, node.num);
        const rowsRaw = slots.map((slot) => {
          const srcKey = `${family}:${slot.from.stage}:${slot.from.num}`;
          const src = recordedFinishers(byKey.get(srcKey)?.r);
          const rider = src[ROLE[slot.role]];
          const placeholder = roleLabel(slot.from.stage, slot.from.num, slot.role);
          return {
            position: null,
            athlete_id: rider?.athlete_id || "",
            name: rider?.name || placeholder,
            bib: rider?.bib || "—",
            time: "Not raced yet",
            pending: true,
            known: Boolean(rider),
            placeholder,
            fromRole: slot.role,
            fromLabel: placeholder,
          };
        });

        if (node.stage === "heat") {
          const seeded = seedStartRows(family, node.stage, node.num);
          const baseRows =
            seeded ||
            (existing?.r || []).map((r) => ({
              ...r,
              pending: true,
              known: true,
              time: "Not raced yet",
            }));
          if (!existing && !seeded) continue;
          projected.push({
            e: existing?.e || syntheticEvent(family, key, node.stage, node.num, items),
            r: [],
            pending: true,
            projectedRows: decorateRows(baseRows, family, node.stage, node.num, byKey, true),
            parsed: { ...node, family, key },
          });
          continue;
        }

        const anySource = slots.some((slot) => {
          const srcKey = `${family}:${slot.from.stage}:${slot.from.num}`;
          return recordedFinishers(byKey.get(srcKey)?.r).length > 0;
        });
        // First knockout round for small fields (Women/Groms semis) uses seed slots;
        // later rounds always project advancement placeholders from the feed rules.
        const seededFirst = !anySource ? seedStartRows(family, node.stage, node.num) : null;
        const pendingRows = seededFirst || rowsRaw;
        projected.push({
          e: existing?.e || syntheticEvent(family, key, node.stage, node.num, items),
          r: [],
          pending: true,
          projectedRows: decorateRows(pendingRows, family, node.stage, node.num, byKey, true),
          parsed: { ...node, family, key },
        });
      }
    }

    return { bracket: projected, extras };
  }

  function stageOfItem(item) {
    if (item.parsed) return navigationStage(item.parsed.stage);
    const n = [item.e.stage, item.e.name, item.e.level].filter(Boolean).join(" ").toLowerCase();
    if (n.includes("qual")) return "Qualifiers";
    if (n.includes("heat")) return "Heats";
    if (n.includes("quarter")) return "Quarters";
    if (n.includes("semi")) return "Semi";
    if (n.includes("final")) return "Finals";
    return "Qualifiers";
  }

  const NAME_ALIASES = {
    "wiktor sowinski": "wickor sowinski",
    "toni sunder": "tobi sunder",
    "lars lottrup": "lars lot trip",
    "radim klaska": "radom klaska",
  };

  function indexSeeds(catalog) {
    seedCatalog = catalog;
    seedIndex = new Map();
    for (const [category, rows] of Object.entries(catalog.categories || {})) {
      for (const row of rows) {
        const entry = {
          seed: row.seed,
          timeSec: row.timeSec,
          time: row.time,
          name: row.name,
          category,
        };
        seedIndex.set(normName(row.name), entry);
      }
    }
    // Alias race-day spellings → TT list keys
    for (const [from, to] of Object.entries(NAME_ALIASES)) {
      const hit = seedIndex.get(to);
      if (hit) seedIndex.set(from, hit);
    }
  }

  async function loadSeeds(url) {
    const res = await fetch(url || "/data/seeds.json", { cache: "no-store" });
    if (!res.ok) throw new Error("seed list missing");
    indexSeeds(await res.json());
    return seedCatalog;
  }

  function loadSeedsData(catalog) {
    indexSeeds(catalog);
    return seedCatalog;
  }

  function parseTimeToSec(time) {
    const s = String(time || "").trim();
    if (!s) return null;
    const parts = s.split(":").map(Number);
    if (parts.some((n) => !Number.isFinite(n))) return null;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 1) return parts[0];
    return null;
  }

  function seedCategoryFromEvent(e) {
    const blob = [e.tournament, e.name, e.stage, e.level].filter(Boolean).join(" ");
    const n = blob.toLowerCase();
    if (n.includes("wild") && n.includes("grom")) return "Wild Groms";
    if (n.includes("wild") && (n.includes("women") || n.includes("womens"))) return "Wild Women";
    if (n.includes("wild") && (/\bopen\b/.test(n) || /\bmen/.test(n))) return "Wild Open";
    if (n.includes("grom")) return "Groms";
    if (n.includes("women") || n.includes("womens")) return "Women";
    if (/\bopen\b/.test(n) || /\bmen/.test(n)) return "Open";
    return null;
  }

  /** RaceTec seeding / qualifying sessions (not knockout heats). */
  function isSeedingEvent(e) {
    const n = [e.name, e.stage, e.level].filter(Boolean).join(" ").toLowerCase();
    if (!n) return false;
    if (/runner\s*ups?|3rd|4th|32nd|infinity|surf|legend|chair|relay/.test(n)) return false;
    if (/\b(heat|quarter|semi|final)s?\b/.test(n) && !/seed|qual|q\s*[12]|tt|time\s*trial/.test(n)) {
      return false;
    }
    return /seed|seeding|qualif|\bq\s*[12]\b|\btt\b|time\s*trial/.test(n);
  }

  function seedingSessionKind(e) {
    const n = [e.name, e.stage].filter(Boolean).join(" ").toLowerCase();
    if (/\bq\s*2\b|qual(?:ifying)?\s*2|session\s*2/.test(n)) return "q2";
    if (/\bq\s*1\b|qual(?:ifying)?\s*1|session\s*1/.test(n)) return "q1";
    return "single";
  }

  function rankRowsByLap(rows) {
    return [...(rows || [])]
      .map((r) => ({
        name: r.name,
        bib: r.bib,
        athlete_id: r.athlete_id,
        time: r.time,
        timeSec: parseTimeToSec(r.time),
      }))
      .filter((r) => r.timeSec != null)
      .sort(
        (a, b) =>
          a.timeSec - b.timeSec ||
          String(a.name || "").localeCompare(String(b.name || ""), undefined, { sensitivity: "base" }),
      );
  }

  function rowsToSeeds(ranked, category, startSeed = 1) {
    return ranked.map((r, i) => ({
      name: r.name,
      time: r.time,
      timeSec: r.timeSec,
      category,
      seed: startSeed + i,
      athlete_id: r.athlete_id,
      bib: r.bib,
    }));
  }

  /**
   * Build seed catalog from live RaceTec events already loaded by the public poll.
   * Open: Q1 locks seeds 17–32 (slowest 16); Q2 locks 1–16 (Q1 laps do not carry).
   * Women/Groms: single session ranks everyone.
   * Returns null if no seeding results yet.
   */
  function buildSeedsFromLiveEvents(eventItems) {
    const byCat = { Open: { q1: null, q2: null, single: null }, Women: { single: null }, Groms: { single: null }, "Wild Open": { single: null }, "Wild Women": { single: null }, "Wild Groms": { single: null } };
    for (const item of eventItems || []) {
      const e = item.e || item;
      if (!isSeedingEvent(e)) continue;
      const cat = seedCategoryFromEvent(e);
      if (!cat || !byCat[cat]) continue;
      const ranked = rankRowsByLap(item.r || []);
      if (!ranked.length) continue;
      const kind = cat === "Open" ? seedingSessionKind(e) : "single";
      if (cat === "Open" && (kind === "q1" || kind === "q2")) byCat.Open[kind] = ranked;
      else byCat[cat].single = ranked;
    }

    const categories = {};
    // Open Q1/Q2
    const openQ1 = byCat.Open.q1;
    const openQ2 = byCat.Open.q2;
    if (openQ1 || openQ2 || byCat.Open.single) {
      const open = [];
      if (openQ1 || openQ2) {
        if (openQ1 && openQ1.length) {
          const slow = openQ1.slice(16); // 17th-fastest onward
          open.push(...rowsToSeeds(slow, "Open", 17));
        }
        if (openQ2 && openQ2.length) {
          open.push(...rowsToSeeds(openQ2.slice(0, 16), "Open", 1));
        }
      } else if (byCat.Open.single) {
        open.push(...rowsToSeeds(byCat.Open.single, "Open", 1));
      }
      open.sort((a, b) => a.seed - b.seed);
      if (open.length) categories.Open = open;
    }
    for (const cat of ["Women", "Groms", "Wild Open", "Wild Women", "Wild Groms"]) {
      if (byCat[cat].single?.length) categories[cat] = rowsToSeeds(byCat[cat].single, cat, 1);
    }
    if (!Object.keys(categories).length) return null;
    return {
      event: "Live seeding race",
      source: "RaceTec public results (polled)",
      updatedAt: new Date().toISOString(),
      categories,
    };
  }

  /** Prefer live RaceTec seeding results; optional static /data/seeds.json fallback. */
  async function refreshSeeds({ eventItems, staticUrl } = {}) {
    const live = buildSeedsFromLiveEvents(eventItems);
    if (live) {
      indexSeeds(live);
      return { catalog: seedCatalog, source: "live" };
    }
    if (staticUrl !== false) {
      try {
        await loadSeeds(staticUrl || "/data/seeds.json");
        return { catalog: seedCatalog, source: "static" };
      } catch (_) {
        /* no static seeds yet */
      }
    }
    if (!seedCatalog) {
      indexSeeds({ event: "Live seeding race", source: "waiting", categories: { Open: [], Women: [], Groms: [], "Wild Open": [], "Wild Women": [], "Wild Groms": [] } });
    }
    return { catalog: seedCatalog, source: "empty" };
  }

  /** Ideal heat/semi grids from seed list. */
  const TOP8_SEMIS = [
    [1, 4, 5, 8],
    [2, 3, 6, 7],
  ];

  function buildCategoryHeatGrids(category) {
    const cat = category || "Open";
    const riders = (seedCatalog?.categories?.[cat] || []).slice();
    const bySeed = new Map(riders.map((r) => [r.seed, r]));
    const count = riders.length || (cat === "Open" ? 32 : 16);

    let groups;
    let titlePrefix;
    if (cat === "Open" || count > 16) {
      groups = EOL_EIGHTH_FINALS_32;
      titlePrefix = "Heat";
    } else if (count > 8) {
      groups = [
        [1, 8, 9, 16],
        [4, 5, 12, 13],
        [2, 7, 10, 15],
        [3, 6, 11, 14],
      ];
      titlePrefix = "Quarter";
    } else {
      groups = TOP8_SEMIS;
      titlePrefix = "Semi";
    }

    return {
      category: cat,
      riderCount: riders.length,
      roundLabel:
        titlePrefix === "Heat"
          ? `Eighth-finals · ${groups.length} heats`
          : titlePrefix === "Quarter"
            ? `Quarters · ${groups.length} heats`
            : `Semis · ${groups.length} heats`,
      heats: groups.map((seeds, hi) => ({
        id: `${cat.toLowerCase()}-${titlePrefix.toLowerCase()}-${hi + 1}`,
        title: `${titlePrefix} ${hi + 1}`,
        subtitle: seeds.join(" · "),
        slots: seeds.map((seed, gi) => {
          const rider = bySeed.get(seed);
          const locked = riderHasTime(rider);
          return {
            startPos: gi + 1,
            seed,
            name: locked ? rider.name : `Seed ${seed}`,
            time: locked ? rider.time || "" : "",
            known: locked,
          };
        }),
      })),
    };
  }

  function buildOpenHeatGrids() {
    return buildCategoryHeatGrids("Open").heats;
  }

  /** Full bracket skeleton with Seed N / Winner-of placeholders (no RaceTec data needed). */
  function previewTournament(category) {
    const cat = category || "Open";
    if (!seedCatalog) {
      indexSeeds({
        event: "Structure preview",
        source: "rules",
        categories: { Open: [], Women: [], Groms: [] },
      });
    }
    const family =
      cat === "Women" ? "women" : cat === "Groms" ? "groms" : cat === "Wildcard" ? "wild-men" : "open-men";
    const riderCount = (seedCatalog?.categories?.[FAMILY_SEED_CAT[family]] || []).length || (family === "open-men" ? 32 : 16);
    const firstStage = family === "open-men" || riderCount > 16 ? "heat" : "quarter";
    const count = firstStage === "heat" ? 8 : 4;
    const items = [];
    for (let i = 1; i <= count; i++) {
      items.push({
        e: {
          id: `preview:${family}:${firstStage}:${i}`,
          name: displayName(family, firstStage, i),
          stage: displayName(family, firstStage, i),
          tournament: cat === "Open" || cat === "Open Men" ? "Open" : cat,
          level: "Bracket",
          highlight_count: 2,
          multi_lap: 0,
          count: 0,
        },
        r: [],
      });
    }
    return enrichTournament(items);
  }

  global.BracketProjection = {
    enrichTournament,
    previewTournament,
    stageOfItem,
    parseHeat,
    navigationStage,
    loadSeeds,
    loadSeedsData,
    refreshSeeds,
    buildSeedsFromLiveEvents,
    isSeedingEvent,
    buildOpenHeatGrids,
    buildCategoryHeatGrids,
    categoryHasTimes,
    riderHasTime,
    EOL_EIGHTH_FINALS_32,
    QF_FROM_HEATS,
    SF_FROM_QUARTERS,
    FINAL_FROM_SEMIS,
    getSeedCatalog: () => seedCatalog,
  };
})(window);
