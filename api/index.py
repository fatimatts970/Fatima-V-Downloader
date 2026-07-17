from flask import Flask, render_template
import os

# Yahan hum explicitly templates folder ka path de rahe hain
app = Flask(__name__, template_folder='../templates')

@app.route('/')
def index():
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
