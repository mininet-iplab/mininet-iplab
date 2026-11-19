## DNSSEC Flagship Lab Guide

DNS answers arrive over an unauthenticated transport: anything on the path can
rewrite one, and a plain Resolver has no way to tell. **DNSSEC** closes that by
signing Resource Records, so a Resolver can verify an answer came from the Zone
that owns it and reached the client unchanged.

The verification is a **chain of trust**. Each Zone signs its own Records, and
its parent vouches for its key by publishing a **DS Record** — a fingerprint of
the child's Key Signing Key. The chain has to hold at every link, all the way
down from a key the Resolver was told to trust in advance.

This lab builds that chain and then breaks it.

```
client1 ──► res1 ──► root1  ( . )            signed, trust anchor
                └──► tld1   ( com. )         signed, DS held by root1
                └──► auth1  ( example.com. ) signed, DS held by tld1
                └──► auth2  ( example.com. ) secondary, serves the same signatures
```

| Node | Address | Role |
|---|---|---|
| `root1` | `10.0.1.2` | Authoritative for `.`, the trust anchor |
| `tld1` | `10.0.2.2` | Authoritative for `com.` |
| `auth1` | `10.0.3.2` | Primary for `example.com.`, holds the private keys |
| `auth2` | `10.0.4.2` | Secondary for `example.com.`, holds no private keys |
| `res1` | `10.0.5.2` | Validating Recursive Resolver |
| `client1` | `10.0.6.2` | The Learner's host |
| `r1` | `10.0.<n>.1` | Router joining all six subnets |

Every Subnet is dual-stack, `10.0.<n>.0/24` and `2001:db8:<n>::/64`, and `r1` runs no routing protocol: this is the arrangement of `dns-lab`, signed, so you can compare the two directly.

Every Zone here is signed with **ECDSA P-256** (algorithm 13) under a split
**KSK/ZSK** key pair, and proves non-existence with **NSEC**.

Everything below runs from `client1`'s terminal unless it says otherwise.
Open one from the Topology view (click `client1`, then **Open Terminal**), or
from the CLI with `./util/connect_node.sh client1`.

Open the **DNS modal** for `auth1` now and leave it up: click the `DNS` badge
above `auth1`. Its header carries a green **Signed** badge, and the **DNSSEC
Details** section below the Records table lists the keys signing the Zone.

The TTL caps on `res1` are deliberately short — 120 seconds for an answer, 30
for a denial — so a cached verdict expires inside a class rather than
outliving it.

---

### 1. Valid resolution: the chain of trust holding

Ask for a name and request the signatures with it:

```bash
dig +dnssec web.example.com @10.0.5.2
```

Two things in that answer are new. In the `ANSWER SECTION` the `A` record is
followed by an **`RRSIG`** — the signature `auth1` made over it, naming
algorithm `13` and the key tag that signed it. And in the header, the `flags:`
line carries **`ad`**:

```
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 41234
;; flags: qr rd ra ad; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1
```

`ad` is **Authenticated Data**. It is `res1` telling the client: *I verified
this, and the chain held*. It is the Resolver's claim, not the signature
itself — which is why it only means something over a link the client trusts.

Watch the Resolver agree. Open the **Resolver modal** on `res1` (the teal
`RESOLVER` badge) and look at the Cache Inspector: the `web.example.com. A`
row now carries a green **SECURE** badge and an **AD** marker beside its HIT
badge. The **Secure Queries** counter in the statistics header has moved too.

To see the whole chain rather than the verdict, use `delv`, which validates on
its own rather than trusting `res1`'s `ad` flag. It needs to be told this
lab's root key, so read it off `root1` first: open `root1`'s **DNS modal**
and read the **DNSSEC Details** section below the Records table. From the Lab
Prompt in CLI Mode the same is:

```
mininet-iplab> dnssec_keys root1 .
```

```
*** DNSSEC Keys for zone . on root1:
    DNSSEC: enabled  NSEC3: disabled
    Tag      Role   Alg   Size   ID
    -------- ------ ----- ------ --------------------------------
    31589    KSK    13    256    a1b2c3...
    52814    ZSK    13    256    d4e5f6...

    DS Records (for parent delegation):
    . 31589 13 2 3C6B7A...
```

Write that DS into an anchor file on `client1` — substituting your own Lab's
key tag and digest, which are generated fresh on every run:

```bash
cat > root.anchor <<'EOF'
trust-anchors { . static-ds 31589 13 2 "3C6B7A..."; };
EOF

delv @10.0.5.2 -a root.anchor web.example.com
```

```
; fully validated
web.example.com.	120	IN	A	10.0.6.100
web.example.com.	120	IN	RRSIG	A 13 3 300 ...
```

**`; fully validated`** is the whole lab in one line: `delv` walked `.` →
`com.` → `example.com.`, checked each Zone's DS against the child's key, and
verified every signature along the way.

---

### 2. The Secondary serves the same proof

`auth2` is a Secondary. It replicates `example.com.` over **AXFR** and holds
**no private keys** — it cannot sign anything. Ask it directly:

```bash
dig +dnssec web.example.com @10.0.4.2
```

Compare the `RRSIG` in that answer with the one `auth1` returned at
`10.0.3.2`. They are **byte-for-byte identical**, because the signature
travelled with the Record over the transfer. The signature authenticates the
*data*, not the server that handed it over, so a Secondary can prove origin
without ever being trusted with the key material.

Open `auth2`'s DNS modal: its Zone is read-only, and the **DNSSEC Details**
section lists no keys — nothing on this Node can sign. That is the point.

---

### 3. Breaking the chain: a stale DS in the parent

Now stage the failure that DNSSEC exists to catch. The link from `com.` to
`example.com.` is the DS Record that `tld1` publishes. Corrupt it, and the
parent is vouching for a key that does not exist.

First read the real DS off `auth1`: its **DNSSEC Details** section carries the
key tag, algorithm, digest type and digest, next to a **Copy DS** button that
puts the RDATA on the clipboard so a 64-character digest never has to be
retyped. From the Lab Prompt in CLI Mode the same is:

```
mininet-iplab> dnssec_keys auth1 example.com.
```

Replace the digest on `tld1` with one that is the right shape and the wrong
value — 64 hex characters of zero. Open `tld1`'s **DNS modal**, **Edit** the
`example.com. DS` row, and save the altered digest: the form accepts `DS` and
checks the digest length before it sends. Keep your Lab's own key tag.

From the Lab Prompt in CLI Mode the same edit is:

```
mininet-iplab> dns_records tld1 com. del example.com. DS
mininet-iplab> dns_records tld1 com. add example.com. DS 12345 13 2 0000000000000000000000000000000000000000000000000000000000000000
```

The old, good answer is still cached on `res1`, so evict it: in `res1`'s
**Resolver modal**, press **Flush Entire Cache**.

Then ask again from `client1`:

```bash
dig +dnssec web.example.com @10.0.5.2
```

```
;; ->>HEADER<<- opcode: QUERY, status: SERVFAIL, id: 8123
```

**`SERVFAIL`**, and no answer at all. This is the correct behaviour and the
thing worth dwelling on: `res1` *received* an answer from `auth1` — a perfectly
valid, correctly signed one — and refused to pass it on, because the parent's
DS no longer matches the key that signed it. A validating Resolver would rather
return nothing than return something it cannot prove.

Ask `delv` what went wrong:

```bash
delv @10.0.5.2 -a root.anchor web.example.com
```

```
;; broken trust chain resolving 'web.example.com/A/IN': 10.0.5.2#53
;; resolution failed: broken trust chain
```

Now look at the Resolver modal. The **Bogus Queries** counter has climbed, and
the `web.example.com.` row that was **SECURE** with an **AD** marker a moment
ago is gone: a Resolver does not cache data it judged bogus, so there is
nothing left to badge. What *is* still there is the rest of the walk — the
`com.` and `.` entries, and the `example.com. DS` itself, all still **SECURE**
with their `AD` markers. The break is exactly one link wide, and the cache says
which link by what it stopped holding.

A row does carry a red **BOGUS** badge when the Resolver is holding an RRset it
has since judged bogus rather than discarding it outright — flipping the digest
back and forth without flushing is the quickest way to catch one mid-flight.

Query `auth1` directly to confirm the authoritative data was never the problem:

```bash
dig +dnssec web.example.com @10.0.3.2
```

`NOERROR`, with the signature intact. `auth1` is fine. The delegation is not.

---

### 4. Repairing the delegation

Put the real digest back on `tld1`, using the value read in step 3: **Edit**
the `example.com. DS` row in `tld1`'s **DNS modal** and save the true digest,
then flush `res1` from its **Resolver modal** with **Flush Entire Cache**.

From the Lab Prompt in CLI Mode the same is:

```
mininet-iplab> dns_records tld1 com. del example.com. DS
mininet-iplab> dns_records tld1 com. add example.com. DS 31589 13 2 <real digest>
mininet-iplab> flush_cache res1
```

Then, from `client1`:

```bash
dig +dnssec web.example.com @10.0.5.2
```

`status: NOERROR`, the `ad` flag is back, and the Resolver's cache rows return
to **SECURE**. Nothing on `auth1` was ever touched; the whole outage lived in
one Record in the parent Zone. That is the operational lesson of DNSSEC — a
delegation whose DS drifts out of step with the child's key takes the child
off the internet, and only the parent can fix it.

---

### 5. Authenticated denial of existence

A signed Zone has to be able to prove a name *absent*, not merely say so — an
unsigned `NXDOMAIN` would be trivially forgeable. Ask for a name that was never
created:

```bash
dig +dnssec missing.example.com @10.0.5.2
```

```
;; ->>HEADER<<- opcode: QUERY, status: NXDOMAIN, id: 2211
;; flags: qr rd ra ad; QUERY: 1, ANSWER: 0, AUTHORITY: 4, ADDITIONAL: 1
```

`NXDOMAIN` — *with* the `ad` flag. The denial itself is authenticated. The
proof is in the `AUTHORITY SECTION`:

```
mail.example.com.	120	IN	NSEC	ns1.example.com. A RRSIG NSEC
mail.example.com.	120	IN	RRSIG	NSEC 13 3 120 ...
```

That **`NSEC`** Record is a signed statement about a *gap*: it says the next
name in the Zone after `mail.example.com.` is `ns1.example.com.`, and lists the
record types `mail` itself has. Since `missing.example.com.` sorts inside that
gap, the signed NSEC proves it does not exist — without `auth1` having to sign
anything on demand.

Ask for a type that the name does not have, rather than a name that is absent:

```bash
dig +dnssec web.example.com MX @10.0.5.2
```

`NOERROR` with an empty answer — **NODATA** — and an NSEC for
`web.example.com.` whose type bitmap lists `A RRSIG NSEC` and no `MX`. The
signed bitmap is the proof.

NSEC has a well-known cost: walking a Zone's NSEC chain enumerates every name
in it. Declare a Zone with `nsec3=True` to hash the names instead; the DNSSEC
Details section then reports `NSEC3` and its salt in place of `NSEC`.

Those denials are cached too, for this lab's 30-second negative TTL — visible
in the Resolver modal as a yellow **NXDOMAIN** badge in the Status column
alongside a green **SECURE** badge and an **AD** marker in the Validation
column. The two columns are separate claims: *answerable from memory* and
*cryptographically proven*.

---

### 6. Rolling the Zone Signing Key

Keys are rotated, and the rotation has to be invisible. The ZSK signs the
Zone's ordinary Records, and — unlike the KSK — is not fingerprinted by the
parent, so replacing one needs no coordination with `tld1` at all.

> **This step is run by your Instructor.** Triggering a key rollover is a
> Lab-wide operation and lives at the Lab Prompt, which is the Instructor's.
> Your part is to watch it happen: keep `auth1`'s **DNS modal** open on the
> **DNSSEC Details** section throughout, and note the key tags before and
> after. The panel polls, so the new key set appears on its own.

Note the current key tags in **DNSSEC Details** first. Then your Instructor
rolls the ZSK:

```
mininet-iplab> dnssec_rollover auth1 example.com. zsk
```

```
Triggered ZSK rollover for example.com. on auth1
```

Read the keys again after a few seconds, in **DNSSEC Details**:

There is now an **additional ZSK** with a new key tag. Both are present at
once, and that overlap is what makes the rollover safe: Knot publishes the new
key and lets Resolvers pick it up before it starts signing with it, and keeps
the old signatures valid until every cached copy of them has expired. The
DNSSEC Details section in `auth1`'s DNS modal shows the same two keys, picked
up on its next poll.

Confirm resolution never faltered: flush `res1` from its **Resolver modal**
with **Flush Entire Cache**, then query again.

```bash
dig +dnssec web.example.com @10.0.5.2
```

`NOERROR`, `ad` still set, **SECURE** in the cache — answered under a key that
did not exist a minute ago. The KSK never moved, so the DS in `com.` is
untouched and the chain of trust never noticed.

Roll the **KSK** instead — again an Instructor-run step — and the picture
changes completely:

```
mininet-iplab> dnssec_rollover auth1 example.com. ksk
```

A new KSK means a new DS, and until `tld1` publishes it the parent is vouching
for the old key — the exact state step 3 staged by hand. That is why a KSK
rollover is a two-party operation and a ZSK rollover is not.

---

### What to take away

- DNSSEC authenticates **data**, not connections. The Secondary in step 2 proved
  origin while holding no key, and `auth1` in step 3 was blameless while the Zone
  was unreachable.
- The `ad` flag is the Resolver's **claim**; the RRSIG chain is the **evidence**.
  `dig` shows the first, `delv` checks the second.
- A validating Resolver answers `SERVFAIL` rather than handing over data it
  cannot prove. An outage is the designed failure mode.
- Denial of existence is signed too, which is why NSEC Records appear in the
  AUTHORITY section of an `NXDOMAIN`.
- A ZSK rollover is local to the Zone. A KSK rollover is a conversation with
  the parent.

---

### Reference

| Command | What it shows |
|---|---|
| `dig +dnssec <name> @10.0.5.2` | The answer, its RRSIGs, and the `ad` flag |
| `delv @10.0.5.2 -a root.anchor <name>` | Independent validation of the whole chain |
| `dnssec_keys <node> <zone>` | Keys signing a Zone and the DS for its parent |
| `dnssec_rollover <node> <zone> [zsk\|ksk]` | On-demand key rollover |
| `dns_records <node> <zone> add <owner> DS <rdata>` | Publish a delegation in the parent |
| `show_cache res1` | Cached entries with `[SECURE]` / `[BOGUS]` / `[INSECURE]` and `AD` |
| `flush_cache res1` | Evict the cache so the next query re-validates |
