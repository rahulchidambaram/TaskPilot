"""A small built-in demo website ("Demo Store") the agent can safely practise on.

It exposes every risk level on purpose, so the safety layer can be demonstrated live and deterministically:
  * browsing / searching products     -> low risk
  * add to cart, send contact message -> medium risk (human approval)
  * Pay now, card fields, sign-in     -> blocked
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/demo", include_in_schema=False)

PRODUCTS = [
    ("Ceramic Mug", 12.50, "Kitchen"), ("Mechanical Keyboard", 89.00, "Electronics"),
    ("Desk Lamp", 34.99, "Home Office"), ("Notebook Set", 9.75, "Stationery"),
    ("USB-C Hub", 45.00, "Electronics"), ("Travel Mug", 18.25, "Kitchen"),
]

CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#1c2430}
header{background:#14213d;color:#fff;padding:14px 24px;display:flex;gap:20px;align-items:center}
header a{color:#fff;text-decoration:none;font-size:15px} header b{font-size:18px;margin-right:auto}
main{max-width:860px;margin:24px auto;padding:0 20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
.product{background:#fff;border:1px solid #e1e4ea;border-radius:10px;padding:16px}
.product h3{margin:0 0 4px}.price{font-weight:700;color:#0b6e4f}.cat{color:#667;font-size:13px}
button,.btn{background:#2f6fed;color:#fff;border:0;border-radius:8px;padding:9px 14px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
input,textarea{width:100%;padding:9px;border:1px solid #c8cdd6;border-radius:8px;margin:4px 0 12px;box-sizing:border-box;font:inherit}
form.card{background:#fff;border:1px solid #e1e4ea;border-radius:10px;padding:20px;max-width:480px}
label{font-weight:600;font-size:14px}.note{color:#667;font-size:13px}
"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="TaskPilot demo store for safe agent practice">
<title>{escape(title)}</title><style>{CSS}</style></head><body>
<header><b>Demo Store</b><a href="/demo/">Home</a><a href="/demo/cart">Cart (<span id="count">0</span>)</a>
<a href="/demo/contact">Contact</a><a href="/demo/login">Sign in</a></header><main>{body}</main>
<script>try{{document.getElementById('count').textContent=JSON.parse(localStorage.cart||'[]').length}}catch(e){{}}</script>
</body></html>""")


@router.get("/")
async def store(q: str = ""):
    items = [p for p in PRODUCTS if q.lower() in p[0].lower() or q.lower() in p[2].lower()]
    cards = "".join(
        f'<div class="product"><h3>{escape(n)}</h3><div class="cat">{escape(c)}</div>'
        f'<p class="price">${pr:.2f}</p><button type="button" onclick="add(\'{escape(n)}\')">Add to cart</button></div>'
        for n, pr, c in items
    ) or "<p>No products match your search.</p>"
    body = f"""<h1>Products</h1>
<form method="get" action="/demo/"><label for="q">Search products</label>
<input id="q" name="q" value="{escape(q)}" placeholder="e.g. mug"><button type="submit">Search</button></form>
<p class="note">{len(items)} product(s) shown</p><div class="grid">{cards}</div>
<script>function add(n){{var c=JSON.parse(localStorage.cart||'[]');c.push(n);localStorage.cart=JSON.stringify(c);
document.getElementById('count').textContent=c.length}}</script>"""
    return page("Demo Store", body)


@router.get("/cart")
async def cart():
    return page("Cart - Demo Store", """<h1>Your cart</h1><ul id="items"></ul>
<p><a class="btn" href="/demo/checkout">Proceed to checkout</a></p>
<script>var c=JSON.parse(localStorage.cart||'[]');document.getElementById('items').innerHTML=c.length?c.map(function(x){return '<li>'+x.replace(/</g,'&lt;')+'</li>'}).join(''):'<li>Cart is empty</li>'</script>""")


@router.get("/checkout")
async def checkout():
    return page("Checkout - Demo Store", """<h1>Checkout</h1><form class="card" method="post" action="/demo/pay">
<label for="card">Card number</label><input id="card" name="card" autocomplete="cc-number" placeholder="Card number">
<label for="cvv">CVV</label><input id="cvv" name="cvv" placeholder="CVV">
<button type="submit">Pay now</button></form><p class="note">Demo only: no payment is ever processed.</p>""")


@router.post("/pay")
async def pay():
    return page("Payment - Demo Store", "<h1>No payment processed</h1><p>This is a demo store.</p>")


@router.get("/contact")
async def contact():
    return page("Contact - Demo Store", """<h1>Contact us</h1><form class="card" method="post" action="/demo/contact">
<label for="name">Name</label><input id="name" name="name">
<label for="email">Email</label><input id="email" name="email" type="email">
<label for="message">Message</label><textarea id="message" name="message" rows="4"></textarea>
<button id="send" type="submit">Send message</button></form>""")


@router.post("/contact")
async def contact_post(name: str = Form(""), email: str = Form(""), message: str = Form("")):
    return page("Thanks - Demo Store",
                f'<h1>Thank you!</h1><p id="confirmation">Thanks, {escape(name)}. We received your message: '
                f'"{escape(message)}" (reply to {escape(email)}).</p>')


@router.get("/login")
async def login():
    return page("Sign in - Demo Store", """<h1>Sign in</h1><form class="card" method="post" action="/demo/login">
<label for="user">Username</label><input id="user" name="user">
<label for="pw">Password</label><input id="pw" name="password" type="password">
<button type="submit">Sign in</button></form>""")


@router.post("/login")
async def login_post():
    return page("Sign in - Demo Store", "<h1>Sign-in is disabled in the demo.</h1>")
