# takeHomeApp

# Alcohol Label Verification (Prototype)

The purpose of this app is to create a tool thta gives a field-by-field pass/fail/review
verdict if the label meets standards. The app runs entirely on your browser
and comes with some sample test data(Which is a zip file that can be gotten in the batch upload section)
for users to see how data works.

## How it Works & What it checks

The app has 2 modes single mode and batch upload mode

**Single mode**
Single mode works as a form. Users type in data about what should be expected in a
Label and upload a lable picture which is then matched to see if content is similar.

**Batch Mode**
Batch mode matches uploaded images to CSV rows by exact filename. The CSV is
parsed client-side (`parseCsv` in `static/script.js`), and each image is then
sent to the same `/verify` endpoint single mode uses, one request per image,
with up to 3 requests in flight at once. Results fill in row-by-row as each
one finishes rather than all at once at the end.

CSV columns: `filename, brand_name, class_type, alcohol_content, net_contents,
government_warning` (the last column is optional — leave it blank to check
against the standard federal text).

| Field              | How it's compared                                                                                                                                                                                                                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Brand Name         | Case/punctuation-insensitive fuzzy match (`"STONE'S THROW"` label ↔ `"Stone's Throw"` form counts as a match)                                                                                                                                                                                           |
| Class/Type         | Same fuzzy approach                                                                                                                                                                                                                                                                                     |
| Alcohol Content    | Parses `%ABV` and `proof` numerically from both sides, compares the numbers (not the formatting), and flags if proof isn't ~2×ABV                                                                                                                                                                       |
| Net Contents       | Parses number + unit (mL/L/fl oz), converts to a common unit, requires an exact numeric match                                                                                                                                                                                                           |
| Government Warning | **Three separate checks**, all of which matter: (1) exact, word-for-word text match against the federal statement, (2) the literal substring `GOVERNMENT WARNING:` must appear in all caps, (3) a best-effort check for whether that phrase is bolder than the surrounding text (see limitations below) |

Each field gets a stamp: **Match**, **Mismatch**, or **Review** (used when the
tool can't confidently parse something and a human should look). The overall
result is only "Approved" if every field is a clean Match.

## Key Requirements & Assumptions

- **Something Fast** Customer wants something faster than their already
  laid out process. Because of this images are downsized to save on time
  and the OCR engine is baked in with Docker so no API calls which could
  slow down the process
- **No Outsource API** Customers network blocks most api calls. Used
  tesseract for image matching since it doesnt require any API calls and just
  needs to be downloaded into system. More on tesseract here:
- **Handles Batch Uploads** During peak times, customer recieves 200+
  labels that needs to be verified. Batch uploads would be necessary here.
  A key assumption for this process is all labels would be in either jpg or png
  and label details would all be in a csv file with correct matching image file name
- **Governement Warning Check(Very Important)** Government warning Must be exactly as is
  no tiny font, no extra words. Must be bold and capitelized.
- **Label details** Another assumption made when building this was label details only
  consist of 5 details which are Brand name, Class/Type, Alcohol Content,
  Net Contents, Govenment Warning

## Known limitations

- **Bold detection is not a certainty.** OCR engines don't
  expose font weight directly. This app compares ink density (dark-pixel
  ratio) in the "GOVERNMENT WARNING:" region against the surrounding body
  text but it can be fooled by lighting
- **OCR accuracy depends on photo quality.** Glare, curved bottles, low
  contrast, and small/stylized fonts all reduce accuracy. The "Review" verdict
  (rather than a false Match or false Mismatch) is the app's way of saying
  "I'm not confident, look at this one yourself."
- **Type of Image File** Typical real submissions would probably be in
  PDF format with multiple views of the label (Front view, back view).
  Currently the app only takes in JPG and PNG files with only one view
  of all content
- **Language Barrier** Right now the app only works with the english language
  so labels with different languages or forms with different languages would not
  be processed properly
- **Batch Upload Format** The batch upload format takes in a single CSV form
  with image file name and other requirements filled out. If image file names
  arent matched correctly or columns in the csv arent matched correctly, output
  will be wrong
- **Data Storage** Data is stored nowhere so large uploads have to be processed
  right after information is given or user would have to re-upload all data again
  since it would be lost when leaving the app
- **Net Contents currently requires an exact numeric match**

## Run it

## Project layout

```
Dockerfile               Bundles Tesseract + all deps into one buildable image
Dockerfile.vercel        Same image, under the filename for Vercel
vercel.json              Declares this as a container-runtime Vercel service
docker-compose.yml       One-command local run (docker compose up --build)
app.py                   Flask routes (/, /verify, /sample_csv)
verifier.py              Per-field matching logic + overall verdict
ocr_engine.py            Tesseract wrapper, preprocessing, bold heuristic
utils.py                 Text normalization, fuzzy ratio, regex extraction
templates/index.html     Page markup (single + batch forms)
static/style.css         Styling
static/script.js         CSV parsing, image resize, per-image batch requests, rendering
sample_data/             Synthetic test labels, batch CSV template
```

## Stack

- **Backend:** Python + Flask, served by gunicorn in production
- **OCR:** Tesseract (via `pytesseract`) + OpenCV for preprocessing — all local, no network calls
- **Frontend:** plain HTML/CSS/JS (no build step, no framework)
- **Matching:** stdlib `difflib` + regex — no extra fuzzy-matching dependency to keep the install list short
- **Packaging:** Docker — Tesseract + all Python deps are baked into the image at build time
