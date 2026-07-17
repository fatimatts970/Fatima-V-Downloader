from flask import Flask, render_template, request, jsonify
import yt_dlp
import os

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../templates'))
app = Flask(__name__, template_folder=template_dir)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/process', methods=['POST'])
def process():
    url = request.form.get('url')
    ydl_opts = {'format': 'best'}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return jsonify({'title': info.get('title'), 'url': info.get('url')})

if __name__ == '__main__':
    app.run(debug=True)
