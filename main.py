import os
from flask import Flask, request, redirect, Response, url_for
from google.cloud import storage
import requests
from google import genai
from PIL import Image
import io
import json
import cv2
import numpy as np
import time



app = Flask(__name__)
storage_client = storage.Client()
BUCKET_NAME = 'cot5930-project-storage'
api_key = os.getenv("GOOGLE_API_KEY")

@app.route("/hello")
def hello_world():
    return "Hello, World!"

@app.route('/serve/<filename>')
def serve_file(filename):
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)

    try:
        print(f"✅ Attempting to fetch {filename} from Cloud Storage.")

        file_data = blob.download_as_bytes()
        print(f"✅ Successfully fetched {filename}, size: {len(file_data)} bytes")

        headers = {
            "Content-Type": blob.content_type if blob.content_type else "application/octet-stream"
        }

        return Response(file_data, headers=headers)
    except Exception as e:
        print(f"❌ Error retrieving file {filename}: {e}")
        return "Error retrieving file", 500

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
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = list(bucket.list_blobs())  # Ensure we get all images

    print(f"✅ Retrieved {len(blobs)} images") 

    index_html = """
    <form method="post" enctype="multipart/form-data" action="/upload">
        <label for="file">Choose file to upload</label>
        <input type="file" id="file" name="form_file" accept="image/jpeg"/>
        <button>Submit</button>
    </form>
    <h2>Uploaded Images</h2>
    <ul>
    """

    new_image_list = "<ul>"
    for blob in blobs:
        if blob.name.endswith((".jpg", ".jpeg", ".png")):
            json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
            try:
                json_blob = bucket.blob(json_filename)
                json_data = json_blob.download_as_string()
                json_info = json.loads(json_data)
                title = json_info.get("title", "No title found")
                description = json_info.get("description", "No description found")
            except Exception as e:
                print(f"Error retrieving JSON for {blob.name}: {e}")
                title, description = "No title", "No description"

            new_image_list += f'''
            <li><img src="/serve/{blob.name}" alt="Uploaded Image" width="200"></li>
            <li><strong>Title: </strong>{title}</li>
            <li><strong>Description: </strong>{description}</li>
            <li>
                <form action="/download/{blob.name}" method="GET">
                    <button type="submit">Download</button>
                </form>
            </li>
            '''
    new_image_list += "</ul>"
    return index_html + new_image_list

@app.route('/download/<filename>')
def download_file(filename):  # Rename function to avoid conflicts
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(filename)
    
    try:
        file_data = blob.download_as_bytes()
        print(f"✅ Successfully fetched {filename} for download")

        # Set Content-Disposition to force file download
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": blob.content_type if blob.content_type else "application/octet-stream"
        }

        return Response(file_data, headers=headers)
    except Exception as e:
        print(f"❌ Error retrieving file {filename}: {e}")
        return "Error retrieving file", 500

@app.route("/upload", methods=["POST"])
def upload():
    print("🔍 Received upload request.")  # Log request start

    if "form_file" not in request.files:
        print("❌ No file found in request.")
        return "No file uploaded", 400

    file = request.files["form_file"]
    if file.filename == "":
        print("❌ Empty file received.")
        return "No selected file", 400

    print(f"✅ Received file: {file.filename}")
    upload_to_gcs(BUCKET_NAME, file)
    
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
    print(f"✅ Uploaded {blob.name} to Cloud Storage.")


def get_blobs_urls():
    print("🟡 Entered get_blobs_urls()")
    try:
        bucket = storage_client.bucket(BUCKET_NAME)
        blobs = bucket.list_blobs(max_results=5)
        
        image_urls = [f"/serve/{blob.name}" for blob in blobs if blob.name.endswith((".jpg", ".jpeg", ".png"))]
        return image_urls
    except Exception as e:
        print(f"❌ Error while fetching blobs: {e}")
        return []
    
def generate_title_description(blob):
    print(f"--- Generating title and description for image: {blob.name} ---")
    start_time = time.time()

    if not api_key:
        print("❌ API key is missing!")
        return "Error", "Error"

    image_url = request.host_url + url_for('serve_file', filename=blob.name)
    print(f"Image URL recieved: {image_url}")

    # ✅ STEP 1: Fetch Image First!
    try:
        image_download_start = time.time()
        response = requests.get(image_url)
        image_download_end = time.time()

        print(f"⏳ Image download took {image_download_end - image_download_start:.2f} seconds.")

        if response.status_code != 200:
            print(f"❌ Error fetching image content: {response.status_code}")
            return "Error fetching title", "Error fetching description"

        if not response.content or len(response.content) < 500:  # Ensure valid content
            print("❌ Image content is empty or too small.")
            return "Error fetching title", "Error fetching description"

        print(f"✅ Image downloaded successfully, size: {len(response.content)} bytes")

    except Exception as e:
        print(f"❌ Image retrieval failed: {e}")
        return "Error fetching title", "Error fetching description"

    # ✅ STEP 2: Open & Process Image AFTER Successfully Fetching It!
    try:
        print("🔍 Attempting to open image with PIL...")
        image_bytes = io.BytesIO(response.content)
        image = Image.open(image_bytes)
        print("✅ PIL successfully opened the image.")

        if image.format not in ["JPEG", "PNG"]:
            print(f"❌ Unsupported image format: {image.format}")
            return "Error fetching title", "Error fetching description"

        print(f"✅ Image format detected: {image.format}")

        image = image.convert("RGB")
        print("✅ Image converted to RGB.")
        image = image.resize((512, 512))
        print("✅ Image resized successfully.")

    except Exception as e:
        print(f"❌ Image processing failed: {e}")
        return "Error fetching title", "Error fetching description"

    # ✅ STEP 3: AFTER Valid Image Processing → Send to AI Model
    try:
        print("🔍 Sending image to AI model for title generation...")
        client = genai.Client(api_key=api_key)
        title_response = client.models.generate_content(
            model="gemini-2.0-flash", contents=[image, "Generate a single, short title for this image."]
        )
        print("✅ AI title generation complete.")

        print("🔍 Sending image to AI model for description generation...")
        description_response = client.models.generate_content(
            model="gemini-2.0-flash", contents=[image, "Generate a short, one-sentence description of this image."]
        )
        print("✅ AI description generation complete.")

    except Exception as e:
        print(f"❌ AI processing failed: {e}")
        return "Error fetching title", "Error fetching description"

    total_time = time.time() - start_time
    print(f"⏳ Total execution time: {total_time:.2f} seconds.")

    return title_response.text, description_response.text



def save_info(blob):
    title, description = generate_title_description(blob)
    json_filename = blob.name.rsplit('.', 1)[0] + '-json.json'
    info = json.dumps({"title": title, "description": description})
    storage_client.bucket(BUCKET_NAME).blob(json_filename).upload_from_string(info, content_type='application/json')
    print(f"✅ Info saved as {json_filename} in Cloud Storage.")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)
