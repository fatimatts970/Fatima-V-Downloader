from flask import Flask, request, jsonify, render_template
import yt_dlp

app = Flask(__name__)

@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.json.get('url')
    ydl_opts = {'format': 'best'}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return jsonify({"url": info['url'], "title": info.get('title')})

if __name__ == '__main__':
    app.run()

