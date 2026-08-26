"""
app.py
Flask entry point. One JSON endpoint (/verify) handles both single-label
and batch verification -- batch mode is just the frontend calling /verify
once per image (see static/script.js) instead of one big multi-file
request. That keeps each request small, which matters on hosts like Vercel
that cap request bodies at 4.5MB. No outbound network calls are made
anywhere in this app -- OCR runs locally via Tesseract, matching runs
locally in Python.
"""
from flask import Flask, jsonify, render_template, request, send_file

import verifier

app = Flask(__name__)

app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024


@app.route("/")
def index():
    return render_template("index.html", standard_warning=verifier.STANDARD_GOVERNMENT_WARNING)


@app.route("/verify", methods=["POST"])
def verify_single():
    image = request.files.get("label_image")
    if not image or image.filename == "":
        return jsonify({"error": "No label image was uploaded."}), 400

    form_data = {
        "brand_name": request.form.get("brand_name", ""),
        "class_type": request.form.get("class_type", ""),
        "alcohol_content": request.form.get("alcohol_content", ""),
        "net_contents": request.form.get("net_contents", ""),
        "government_warning": request.form.get("government_warning", ""),
    }

    try:
        result = verifier.verify_label(form_data, image.stream)
    except Exception as exc:  # surfaced to the UI rather than a raw 500 page
        return jsonify({"error": f"Could not process image: {exc}"}), 422

    result["filename"] = image.filename
    return jsonify(result)


@app.route("/sample_csv")
def sample_csv():
    path = "sample_data/sample.zip"
    return send_file(path, as_attachment=True, download_name="sample.zip", mimetype="application/zip")


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "That image is too large even after client-side resizing. Try a smaller photo."}), 413


if __name__ == "__main__":
    # Local dev convenience only -- in Docker/production, gunicorn imports
    # `app` directly and this block never runs (see Dockerfile CMD).
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="127.0.0.1", port=port)
