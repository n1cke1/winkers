from flask import Flask, render_template

app = Flask(__name__)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/products")
def product_list():
    return render_template("products/list.html")


@app.post("/products")
def product_create():
    return render_template("products/list.html")


@app.get("/about")
def about():
    return "About"
