from flask import Flask, render_template, request
import os

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../templates'))
app = Flask(__name__, template_folder=template_dir)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    video_url = request.form.get('url')
    # Yahan hum baad mein downloader library (yt-dlp) add karenge
    return f"<h1>Link mil gayi!</h1><p>{video_url}</p><p>Video processing shuru ho rahi hai...</p>"

if __name__ == '__main__':
    app.run(debug=True)
