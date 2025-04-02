import os
from flask import Flask, request, redirect
from google.cloud import storage
import datetime
import requests
from google import genai
from PIL import Image
import io
import json
from google.api_core.exceptions import GoogleAPIError

app = Flask(__name__)

storage_client = storage.Client()
BUCKET_NAME = 'cot5930-project-storage'
api_key = os.getenv("GOOGLE_API_KEY")

# ----> URL generation strategy: centralized for flexibility <---- #
def generate_private_url(blob, expiration_minutes=2):
    """
    Replace this function to change how private URLs are generated.
    Options: signed URL, token-based access, internal-only routing, etc.
    """
    try:
        print(f"Generating access URL for: {blob.name}")
        url = blob.generate_signed_url(
            version="v4",
            expiration=datetime.timedelta(minutes=expiration_minutes),
            method="GET"
        )
        return url
    except Exception as e:
        print(f"Error generating access URL for {blob.name}: {e}")
        return None

@app.route("/hello")
def hello_world():
    return "Hello, World!"

@app.route("/MyHealthCheck")
def health_check():
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = list(bucket.list_blobs(max_results=5))
        return "✅ GCS is accessible.", 200
    except Exception as e:
        return f"❌ GCS access failed: {e}", 500

@app.route("/")
def index():
    image_urls = get_blobs_urls()
    print(f"✅ Retrieved {len(image_urls)} image URLs")

    index_html = """
    <form method="post" enctype="multipart/form-data" action="/upload">
        <div>
            <label for="file">Choose file to upload</label>
            <input type="file" id="file" name="form_file" accept="image/jpeg"/>
        </div>
        <div>
            <button>Submit</button>
        </div>
    </form>
    <h2>Uploaded Images</h2>
    <ul>
    """

    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs()
    new_image_list = "<ul>"

    for blob in blobs:
        if blob.name.endswith((".jpg", ".jpeg", ".png")):
            access_url = generate_private_url(blob)
            if not access_url:
                continue

            json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
            try:
                json_blob = bucket.blob(json_filename)
                json_data = json_blob.download_as_string()
                json_info = json.loads(json_data)
                title = json_info.get("title", "No title found")
                description = json_info.get("description", "No description found")
            except Exception as e:
                print(f"Error retrieving JSON for {blob.name}: {e}")
                title = "No title"
                description = "No description"

            new_image_list += f'''
            <li><img src="{access_url}" alt="Uploaded Image" width="200"></li>
            <li><strong>Title: </strong>{title}</li>
            <li><strong>Description: </strong>{description}</li>
            <li>
                <form action="/download" method="GET">
                    <input type="hidden" name="file_url" value="{access_url}">
                    <button type="submit">Download</button>
                </form>
            </li>
            '''

    new_image_list += "</ul>"
    return index_html + new_image_list

@app.route("/download", methods=["GET"])
def download():
    file_url = request.args.get("file_url")
    return redirect(file_url)

@app.route("/upload", methods=["POST"])
def upload():
    if "form_file" not in request.files:
        return "No file uploaded", 400

    file = request.files["form_file"]
    if file.filename == "":
        return "No selected file", 400

    upload_url = upload_to_gcs(BUCKET_NAME, file)
    blob = storage_client.bucket(BUCKET_NAME).blob(file.filename)
    save_info(blob)
    return redirect("/")

@app.route("/files")
def list_files():
    return get_blobs_urls()

def upload_to_gcs(bucket_name, file):
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(file.filename)
    file.seek(0)
    blob.upload_from_file(file)
    return generate_private_url(blob)

def get_blobs_urls():
    print("🟡 Entered get_blobs_urls()")
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = bucket.list_blobs(max_results=5)
        return [generate_private_url(blob) for blob in blobs if blob.name.endswith((".jpg", ".jpeg", ".png"))]
    except Exception as e:
        print(f"❌ Error while fetching blobs: {e}")
        return []

def generate_title_description(blob):
    print(f"--- Generating title and description for image: {blob.name} ---")
    if not api_key:
        print("API key is missing!")
        return "Error", "Error"

    signed_url = generate_private_url(blob)
    if not signed_url:
        return "Error", "Error"

    response = requests.get(signed_url)
    if response.status_code == 200:
        image = Image.open(io.BytesIO(response.content))
        client = genai.Client(api_key=api_key)
        title_response = client.models.generate_content(model="gemini-2.0-flash", contents=[image, "Generate a single, short title for this image."])
        description_response = client.models.generate_content(model="gemini-2.0-flash", contents=[image, "Generate a short, one-sentence description of this image."])
        return title_response.text, description_response.text
    else:
        return "Error fetching title", "Error fetching description"

def save_info(blob):
    title, description = generate_title_description(blob)
    json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
    info = json.dumps({"title": title, "description": description})
    storage_client.bucket(BUCKET_NAME).blob(json_filename).upload_from_string(info, content_type='application/json')
    print(f"Info saved as {json_filename} in Cloud Storage.")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)