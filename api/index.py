from flask import Flask, render_template, request
import os

template_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../templates'))
app = Flask(__name__, template_folder=template_dir)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download', methods=['POST'])
def download():
    url = request.form.get('url')
    return f"Aapne ye link bheji hai: {url}"

if __name__ == '__main__':
    app.run(debug=True)
