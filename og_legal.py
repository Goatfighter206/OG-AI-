"""Legal pages for OG AI: /privacy and /terms.

Plain, static HTML served by two GET routes. These pages exist so
visitors (and OAuth providers reviewing the app, e.g. Google) can
load a real privacy policy and terms from the site itself. The text
describes what OG actually does — cookie-based visitors, per-visitor
memory, optional OAuth connects, Stripe billing — nothing more.

app.py binding (only lines added there):
    import og_legal as _og_legal
    _og_legal.register_legal_routes(app)
"""

_CSS = """
body{background:#121212;color:#e8e8e8;font-family:system-ui,-apple-system,
'Segoe UI',Roboto,Helvetica,Arial,sans-serif;line-height:1.65;margin:0}
main{max-width:760px;margin:0 auto;padding:32px 20px 64px}
h1{color:#ffc107;font-size:1.9rem;margin:0 0 4px}
h2{color:#ffc107;font-size:1.15rem;margin:28px 0 6px}
p,li{font-size:1rem}
a{color:#ffc107}
.eff{color:#a8a8a8;margin:0 0 8px}
.home{display:inline-block;margin-top:36px}
ul{padding-left:22px}
"""


def _doc(title, body):
    return ("<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n"
            "<meta charset=\"UTF-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, "
            "initial-scale=1.0\">\n"
            "<title>" + title + "</title>\n<style>" + _CSS + "</style>\n"
            "</head>\n<body>\n<main>\n" + body + "\n"
            "<a class=\"home\" href=\"/\">&larr; Back to OG AI</a>\n"
            "</main>\n</body>\n</html>\n")


_PRIVACY_BODY = """
<h1>OG AI &mdash; Privacy Policy</h1>
<p class="eff">Effective: October 8, 2026</p>
<p>OG AI (&ldquo;OG&rdquo;) is an 18+ AI chat assistant at
og-ai-service.onrender.com, operated by Brent Williamson
(&ldquo;we&rdquo;, &ldquo;us&rdquo;). Contact:
<a href="mailto:williamson.bt@gmail.com">williamson.bt@gmail.com</a>.
This policy explains, in plain language, what OG collects, why, and
the choices you have.</p>

<h2>No account with OG</h2>
<p>You never sign up with OG and never give OG a password. OG
recognizes your browser with a random cookie called
<code>ogai_uid</code>. Other cookies remember your plan tier (if you
subscribe) and that you confirmed you are 18 or older. If you clear
your cookies, OG treats you as a brand-new visitor.</p>

<h2>What OG stores</h2>
<ul>
<li><strong>Your conversations.</strong> Chat history and memory tied
to your visitor cookie, stored in a database (Postgres), so OG can
pick your conversation back up between visits.</li>
<li><strong>Files.</strong> A file you upload is kept temporarily
(about 24 hours) so OG can read and discuss it. If you save a file to
your storage locker, it stays there until you delete it.</li>
<li><strong>Usage counters.</strong> Per-day counts (messages, images,
lookups, and similar) used to run the free and paid plan limits.</li>
<li><strong>A browser profile ID.</strong> If you use OG&rsquo;s
browser, OG stores the ID of your saved browser profile (held by
Steel) so your log-ins there can be kept between visits, as
described below. Nothing else about the profile is stored by
OG.</li>
</ul>
<p>You can clear your chat memory at any time with the Clear chat /
reset option, and you can delete locker files at any time.</p>

<h2>Optional account connects</h2>
<p>The menu offers optional &ldquo;Connect&rdquo; buttons. Connecting
is always your choice, and every connect uses that service&rsquo;s
official OAuth sign-in. OG <strong>never</strong> receives or stores
your password for those services. The access token OG receives is
stored on OG&rsquo;s server, tied to your visitor cookie, and used
<strong>only</strong> to carry out your own requests in chat.
Disconnecting stops OG&rsquo;s access.</p>
<ul>
<li><strong>Google:</strong> if you connect Google, OG can &mdash;
only when you ask &mdash; search and read your Gmail, read your
calendar and add events you dictate, search and read your Google
Drive files, and create and list Google Tasks reminders.</li>
<li><strong>YouTube, Spotify, GitHub, Discord, Twitch:</strong>
read-only access to your account information on those services (for
example your playlists, subscriptions, followed channels,
repositories, or servers), used only when you ask OG about them.</li>
</ul>
<p>OG AI&rsquo;s use and transfer to any other app of information
received from Google APIs will adhere to the Google API Services User
Data Policy, including the Limited Use requirements.</p>
<p>In plain words: Google user data is <strong>not sold</strong>, is
<strong>not used for advertising</strong>, and is <strong>not shared
with third parties</strong> &mdash; except the AI processing described
below (for example, when you ask OG to read an email, its text goes to
the AI model so OG can answer you). No human reads your connected
data except with your consent, to keep the service secure, to provide
support you asked for, or as required by law.</p>

<h2>OG&rsquo;s browser, saved log-ins, and watches</h2>
<p>OG can operate a real cloud web browser (provided by Steel) that
you watch live and can take over at any time. If you log into a
website inside that browser, you always type your own log-in
yourself &mdash; OG never sees, types, or stores your password.</p>
<ul>
<li><strong>Saved log-ins.</strong> By default, a log-in you make in
OG&rsquo;s browser is kept in a saved browser profile held by
Steel, so you don&rsquo;t have to sign in again on your next visit.
On OG&rsquo;s own server, the only thing stored is that
profile&rsquo;s ID &mdash; never a password and never the profile&rsquo;s
contents. A saved log-in lasts until you tell OG &ldquo;forget my
logins&rdquo; (which deletes the Steel profile and stops any watches
that depended on it), you log out of that site, the site expires the
log-in itself, or the log-in sits unused for about 30 days. Closing
OG&rsquo;s browser is not logging out: the browser itself fully
shuts down between tasks, and the saved profile is what lets it
reopen already logged in.</li>
<li><strong>Watches.</strong> You can ask OG to check Facebook
Marketplace for items while you&rsquo;re away. Those checks are
read-only (searches and listings only), run on a schedule set by
your plan, and draw from a separate daily background-time budget.
OG never messages a seller, buys anything, or posts on its own
&mdash; matches are shown to you in chat, and messaging a seller
always goes through the usual draft-and-approve flow with your
explicit yes first. If Facebook asks for a fresh log-in, the watch
pauses itself and tells you; OG never answers identity checks for
you.</li>
</ul>

<h2>How answers are made (AI processing)</h2>
<p>To answer you, your messages &mdash; and data you ask OG to fetch,
such as an email you ask OG to read &mdash; are sent to AI model
providers (currently OpenAI) for processing. Web lookups and tools
(weather, maps, prices, and similar) use public data services.</p>

<h2>Payments</h2>
<p>Subscriptions are processed by Stripe. OG never sees or stores
your card number. Stripe notifies OG (through its webhook) that a
subscription is active so your plan can be switched on.</p>

<h2>Who else receives data</h2>
<p>Only the service providers needed to run OG &mdash; hosting
(Render), the database and file-storage providers, Stripe (payments),
OpenAI (AI answers), and Steel (the cloud browser and its saved
log-in profiles) &mdash; and anyone we must share with to
comply with the law. We never sell personal data. Ever.</p>

<h2>How long things are kept, and your choices</h2>
<p>Chat memory is kept until you clear it. Locker files are kept
until you delete them. Temporary uploads are deleted after about 24
hours. You can disconnect any connected account, clear your memory,
and delete your files at any time. For questions, or to ask for your
data to be deleted, email
<a href="mailto:williamson.bt@gmail.com">williamson.bt@gmail.com</a>.</p>

<h2>Adults only</h2>
<p>OG is for adults 18 and older only.</p>

<h2>Changes</h2>
<p>If this policy changes, the updated version will be posted on this
page.</p>
"""

_TERMS_BODY = """
<h1>OG AI &mdash; Terms of Service</h1>
<p class="eff">Effective: October 8, 2026</p>
<p>OG AI (&ldquo;OG&rdquo;) is an 18+ AI chat assistant at
og-ai-service.onrender.com, operated by Brent Williamson. Contact:
<a href="mailto:williamson.bt@gmail.com">williamson.bt@gmail.com</a>.
By using OG, you agree to these terms.</p>

<h2>Adults only</h2>
<p>You must be 18 or older to use OG. If you are not, do not use the
service.</p>

<h2>The service, &ldquo;as is&rdquo;</h2>
<p>OG is provided &ldquo;as is&rdquo; and &ldquo;as available&rdquo;,
without warranties of any kind. We do not promise the service will
always be up, fast, or error-free.</p>

<h2>Subscriptions</h2>
<p>Paid plans are billed through Stripe. You can cancel at any time;
cancellation stops future billing, and your paid access runs until
the end of the period you already paid for.</p>

<h2>Acceptable use</h2>
<p>Do not use OG for anything illegal. Do not abuse, attack,
overload, or try to break the service. Do not try to access, extract,
or expose another visitor&rsquo;s data, conversations, or files.</p>

<h2>Answers can be wrong</h2>
<p>OG&rsquo;s answers and tool results can be wrong, incomplete, or
out of date. Check anything important &mdash; money, health, legal,
safety &mdash; against a reliable source before acting on it.</p>

<h2>Liability</h2>
<p>To the extent permitted by law, our liability for anything arising
from your use of OG is limited, and we are not responsible for
indirect or consequential losses.</p>

<h2>Suspension</h2>
<p>We may suspend or cut off access for abuse of these terms or of
the service.</p>

<h2>Changes</h2>
<p>Updated terms will be posted on this page.</p>
"""

PRIVACY_HTML = _doc("OG AI — Privacy Policy", _PRIVACY_BODY)
TERMS_HTML = _doc("OG AI — Terms of Service", _TERMS_BODY)


def register_legal_routes(app):
    """Mount GET /privacy and GET /terms (static legal pages)."""
    from fastapi.responses import HTMLResponse

    @app.get("/privacy", response_class=HTMLResponse)
    async def privacy_page():
        return PRIVACY_HTML

    @app.get("/terms", response_class=HTMLResponse)
    async def terms_page():
        return TERMS_HTML
