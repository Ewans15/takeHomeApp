// Label Desk — frontend wiring. No frameworks; two small fetch-based forms.
//
// Batch mode sends ONE image per HTTP request (reusing the same /verify
// endpoint single mode uses) instead of bundling every image into one big
// request. This matters on Vercel (and similar serverless hosts), which cap
// request bodies at 4.5MB -- a handful of phone photos in one request would
// blow past that immediately. The CSV is parsed here in the browser, so it
// never needs to touch the server at all.

(function () {
  "use strict";

  // ================================================================
  // CSV parsing (RFC4180-ish: quoted fields, escaped "" quotes, commas
  // inside quotes). Small and dependency-free on purpose.
  // ================================================================

  function parseCsv(text) {
    const rows = [];
    let row = [];
    let field = "";
    let inQuotes = false;
    // Normalize line endings so \r\n and \r don't create phantom rows.
    const src = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

    for (let i = 0; i < src.length; i++) {
      const ch = src[i];

      if (inQuotes) {
        if (ch === '"') {
          if (src[i + 1] === '"') {
            field += '"';
            i++;
          } else {
            inQuotes = false;
          }
        } else {
          field += ch;
        }
        continue;
      }

      if (ch === '"') {
        inQuotes = true;
      } else if (ch === ",") {
        row.push(field);
        field = "";
      } else if (ch === "\n") {
        row.push(field);
        rows.push(row);
        row = [];
        field = "";
      } else {
        field += ch;
      }
    }
    // last field/row (files don't always end with a trailing newline)
    if (field.length > 0 || row.length > 0) {
      row.push(field);
      rows.push(row);
    }

    const nonEmptyRows = rows.filter((r) => !(r.length === 1 && r[0].trim() === ""));
    if (nonEmptyRows.length === 0) return [];

    const header = nonEmptyRows[0].map((h) => h.trim());
    return nonEmptyRows.slice(1).map((r) => {
      const obj = {};
      header.forEach((key, idx) => {
        obj[key] = (r[idx] !== undefined ? r[idx] : "").trim();
      });
      return obj;
    });
  }

  // ================================================================
  // Image resize -- downscales+recompresses before upload so a phone
  // photo (often 3-10MB) comfortably clears the 4.5MB request-body cap
  // that Vercel (and many serverless hosts) enforce. The OCR pipeline
  // itself caps processing at ~2200px on the longest side anyway, so
  // resizing client-side to 1800px loses no real accuracy.
  // ================================================================

  const RESIZE_MAX_DIMENSION = 1800;
  const RESIZE_JPEG_QUALITY = 0.87;

  function resizeImageFile(file, maxDim = RESIZE_MAX_DIMENSION, quality = RESIZE_JPEG_QUALITY) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      const objectUrl = URL.createObjectURL(file);

      img.onload = () => {
        URL.revokeObjectURL(objectUrl);
        const longest = Math.max(img.width, img.height);
        const scale = longest > maxDim ? maxDim / longest : 1;
        const w = Math.round(img.width * scale);
        const h = Math.round(img.height * scale);

        const canvas = document.createElement("canvas");
        canvas.width = w;
        canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);

        canvas.toBlob(
          (blob) => {
            if (!blob) {
              reject(new Error("Could not process this image."));
              return;
            }
            resolve(blob);
          },
          "image/jpeg",
          quality
        );
      };
      img.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        reject(new Error("Could not read this image file."));
      };
      img.src = objectUrl;
    });
  }

  // ================================================================
  // Concurrency-capped async pool. Runs `worker` over `items` with at
  // most `limit` in flight at once, calling `onEach(result, item, index)`
  // as each one finishes (so the UI can render incrementally rather than
  // waiting for the whole batch).
  // ================================================================

  async function runPool(items, limit, worker, onEach) {
    let nextIndex = 0;
    async function runNext() {
      while (nextIndex < items.length) {
        const i = nextIndex++;
        const item = items[i];
        let result;
        try {
          result = { ok: true, value: await worker(item, i) };
        } catch (err) {
          result = { ok: false, error: err };
        }
        onEach(result, item, i);
      }
    }
    const workers = Array.from({ length: Math.min(limit, items.length) }, runNext);
    await Promise.all(workers);
  }

  // expose for the smoke tests in sample_data/js_logic_smoketest.mjs
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { parseCsv, runPool };
  }

  if (typeof document === "undefined") return; // running under node for tests only

  // ---------------------------------------------------------------- mode switch
  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mode-btn").forEach((b) => {
        b.classList.remove("is-active");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-active");
      btn.setAttribute("aria-selected", "true");

      document.querySelectorAll(".panel").forEach((p) => p.classList.remove("is-active"));
      document.getElementById(`panel-${btn.dataset.mode}`).classList.add("is-active");
    });
  });

  // ---------------------------------------------------------------- helpers

  function verdictLabel(v) {
    return { pass: "Match", fail: "Mismatch", review: "Review" }[v] || v;
  }

  function overallLabel(v) {
    return v === "pass" ? "Approved" : v === "fail" ? "Mismatch" : "Needs Review";
  }

  function renderFieldResult(container, field) {
    const tpl = document.getElementById("field-row-template").content.cloneNode(true);
    tpl.querySelector(".verdict-stamp").textContent = verdictLabel(field.verdict);
    tpl.querySelector(".verdict-stamp").classList.add(field.verdict);
    tpl.querySelector(".field-result-name").textContent = field.field;
    tpl.querySelector(".field-result-detail").textContent = field.detail;
    container.appendChild(tpl);
  }

  function renderResult(result, resultsEl, statusEl, rawWrapEl, rawTextEl) {
    statusEl.innerHTML = "";
    const stamp = document.createElement("span");
    stamp.className = `overall-stamp ${result.overall}`;
    stamp.textContent = overallLabel(result.overall);
    statusEl.appendChild(stamp);

    const meta = document.createElement("p");
    meta.className = "overall-meta";
    meta.textContent = `${result.filename || ""} · checked in ${result.elapsed_seconds}s`;
    statusEl.appendChild(meta);

    resultsEl.innerHTML = "";
    result.fields.forEach((f) => renderFieldResult(resultsEl, f));

    if (rawWrapEl && rawTextEl) {
      rawTextEl.textContent = result.raw_ocr_text || "(no text detected)";
      rawWrapEl.classList.remove("hidden");
    }
  }

  // ---------------------------------------------------------------- single mode

  const singleForm = document.getElementById("single-form");
  const singleStatus = document.getElementById("single-status");
  const singleSpinner = document.getElementById("single-spinner");
  const singleResults = document.getElementById("single-results");
  const singleRawWrap = document.getElementById("single-raw-wrap");
  const singleRawText = document.getElementById("single-raw-text");

  async function verifyOne(formFields, imageFile) {
    const resized = await resizeImageFile(imageFile);
    const fd = new FormData();
    Object.entries(formFields).forEach(([k, v]) => fd.append(k, v ?? ""));
    fd.append("label_image", resized, imageFile.name || "label.jpg");

    const resp = await fetch("/verify", { method: "POST", body: fd });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || "Something went wrong.");
    return data;
  }

  singleForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitBtn = singleForm.querySelector("button[type=submit]");
    const fileInput = singleForm.querySelector('input[name="label_image"]');
    const imageFile = fileInput.files[0];

    submitBtn.disabled = true;
    singleStatus.innerHTML = "";
    singleResults.innerHTML = "";
    singleRawWrap.classList.add("hidden");
    singleSpinner.classList.remove("hidden");

    try {
      const fields = {
        brand_name: singleForm.brand_name.value,
        class_type: singleForm.class_type.value,
        alcohol_content: singleForm.alcohol_content.value,
        net_contents: singleForm.net_contents.value,
        government_warning: singleForm.government_warning.value,
      };
      const data = await verifyOne(fields, imageFile);
      renderResult(data, singleResults, singleStatus, singleRawWrap, singleRawText);
    } catch (err) {
      singleStatus.innerHTML = `<p class="batch-error">${err.message}</p>`;
    } finally {
      singleSpinner.classList.add("hidden");
      submitBtn.disabled = false;
    }
  });

  // ---------------------------------------------------------------- batch mode
  // One /verify request per image, up to BATCH_CONCURRENCY in flight at once.
  // The CSV never leaves the browser -- it's parsed locally and used only to
  // look up each image's expected field values by filename.

  const BATCH_CONCURRENCY = 3;

  const batchForm = document.getElementById("batch-form");
  const batchStatus = document.getElementById("batch-status");
  const batchSpinner = document.getElementById("batch-spinner");
  const batchResults = document.getElementById("batch-results");

  function addBatchRow(filename) {
    const row = document.createElement("div");
    row.className = "batch-row";
    row.dataset.filename = filename;

    const fileCol = document.createElement("div");
    fileCol.className = "batch-row-file";
    fileCol.textContent = filename;
    row.appendChild(fileCol);

    const fieldsCol = document.createElement("div");
    fieldsCol.className = "batch-row-fields";
    fieldsCol.innerHTML = `<p class="muted">Checking…</p>`;
    row.appendChild(fieldsCol);

    batchResults.appendChild(row);
    return fieldsCol;
  }

  function fillBatchRow(fieldsCol, outcome) {
    fieldsCol.innerHTML = "";
    if (!outcome.ok) {
      fieldsCol.innerHTML = `<p class="batch-error">${outcome.error.message}</p>`;
      return;
    }
    const result = outcome.value;
    const stamp = document.createElement("span");
    stamp.className = `overall-stamp ${result.overall}`;
    stamp.style.fontSize = "15px";
    stamp.style.padding = "4px 12px";
    stamp.textContent = overallLabel(result.overall);
    fieldsCol.appendChild(stamp);

    const list = document.createElement("div");
    result.fields.forEach((f) => renderFieldResult(list, f));
    fieldsCol.appendChild(list);
  }

  function updateBatchSummary(outcomes) {
    const done = outcomes.filter(Boolean);
    const passCount = done.filter((o) => o.ok && o.value.overall === "pass").length;
    const failCount = done.filter((o) => o.ok && o.value.overall === "fail").length;
    const reviewCount = done.filter((o) => o.ok && o.value.overall === "review").length;
    const errorCount = done.filter((o) => !o.ok).length;
    batchStatus.innerHTML = `<p><strong>${done.length}</strong> of <strong>${outcomes.length}</strong> labels checked — `
      + `${passCount} approved, ${failCount} mismatched, ${reviewCount} need review`
      + (errorCount ? `, ${errorCount} could not be processed` : "") + `.</p>`;
  }

  batchForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submitBtn = batchForm.querySelector("button[type=submit]");
    const imageFiles = Array.from(batchForm.querySelector('input[name="label_images"]').files);
    const csvFile = batchForm.querySelector('input[name="batch_csv"]').files[0];

    submitBtn.disabled = true;
    batchStatus.innerHTML = "";
    batchResults.innerHTML = "";
    batchSpinner.classList.remove("hidden");

    try {
      const csvText = await csvFile.text();
      const rows = parseCsv(csvText);
      const rowsByFilename = new Map(rows.map((r) => [r.filename, r]));

      // Pair each uploaded image with its CSV row up front so unmatched
      // files/rows are reported immediately rather than silently skipped.
      const jobs = [];
      const rowFilenamesSeen = new Set();
      imageFiles.forEach((file) => {
        const row = rowsByFilename.get(file.name);
        rowFilenamesSeen.add(file.name);
        jobs.push({ file, row });
      });
      rows.forEach((row) => {
        if (!rowFilenamesSeen.has(row.filename)) {
          jobs.push({ file: null, row, missingImage: true });
        }
      });

      const outcomes = new Array(jobs.length).fill(null);
      const rowEls = jobs.map((job) => addBatchRow(job.file ? job.file.name : job.row.filename));

      await runPool(
        jobs,
        BATCH_CONCURRENCY,
        async (job) => {
          if (job.missingImage) {
            throw new Error("Listed in the CSV but no matching image was uploaded.");
          }
          if (!job.row) {
            throw new Error("No matching row in the batch CSV for this filename.");
          }
          const data = await verifyOne(
            {
              brand_name: job.row.brand_name,
              class_type: job.row.class_type,
              alcohol_content: job.row.alcohol_content,
              net_contents: job.row.net_contents,
              government_warning: job.row.government_warning,
            },
            job.file
          );
          data.filename = job.file.name;
          return data;
        },
        (outcome, job, index) => {
          outcomes[index] = outcome;
          fillBatchRow(rowEls[index], outcome);
          updateBatchSummary(outcomes);
        }
      );
    } catch (err) {
      batchStatus.innerHTML = `<p class="batch-error">${err.message}</p>`;
    } finally {
      batchSpinner.classList.add("hidden");
      submitBtn.disabled = false;
    }
  });
})();
