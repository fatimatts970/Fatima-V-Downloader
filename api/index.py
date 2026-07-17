from flask import Flask, render_template

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

# Agar aapke paas aur routes ya logic hai, toh yahan add karein.
# Yaad rakhein: Vercel ko sirf 'app' variable chahiye.

if __name__ == '__main__':
    app.run(debug=True)
