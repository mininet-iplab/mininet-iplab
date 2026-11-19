## DNS Lab Guide

This lab is the whole DNS resolution chain on one Router. `root1`, `tld1` and `auth1` are **authoritative**: each knows its own Zone and nothing else. `auth2` is a **Secondary** that copies `auth1`'s Zones over the wire. `res1` is a **Recursive Caching Resolver**: it knows nothing to begin with, and answers `client1` by asking the others in turn — an **Iterative Walk** — then caching what it learns.

```text
client1 ──► res1 ──► root1  ( . )
                └──► tld1   ( com. )
                └──► auth1  ( example.com., 10.in-addr.arpa. )  Primary
                └──► auth2  ( same two Zones )                  Secondary
```

| Node | IPv4 | IPv6 | Role |
|---|---|---|---|
| `root1` | `10.0.1.2` | `2001:db8:1::2` | Authoritative for `.`, delegates `com.` and `10.in-addr.arpa.` |
| `tld1` | `10.0.2.2` | `2001:db8:2::2` | Authoritative for `com.`, delegates `example.com.` |
| `auth1` | `10.0.3.2` | `2001:db8:3::2` | Primary for `example.com.` and `10.in-addr.arpa.` |
| `auth2` | `10.0.4.2` | `2001:db8:4::2` | Secondary for both Zones |
| `res1` | `10.0.5.2` | `2001:db8:5::2` | Recursive Caching Resolver |
| `client1` | `10.0.6.2` | `2001:db8:6::2` | The Learner's host, naming `res1` and nothing else |
| `r1` | `10.0.N.1` | `2001:db8:N::1` | Router joining the six Subnets |

Every Node is on its own Subnet and `r1` has no routing protocol: every route is a connected one. A link you disconnect and reconnect comes back by itself.

Everything below runs from `client1`'s Terminal unless it says otherwise. Open one from the Topology view (click `client1`, then **Open Terminal**), or from the CLI with `./util/connect_node.sh client1`.

The TTL caps on `res1` are deliberately short — 120 seconds for an answer, 30 for a denial — so a cached entry expires inside a class rather than outliving it.

---

### 1. Cold resolution: the Iterative Walk

Open the **Resolver modal** and leave it up: click the teal `RESOLVER` badge above `res1`. Press **Flush Entire Cache** so you start empty, then ask for a name:

```bash
dig web.example.com A
```

`res1` asked `root1` for the `com.` referral, `tld1` for the `example.com.` referral, and a nameserver of `example.com.` for the answer, before it could reply. Everything here is a few microseconds away, so `Query time:` is only about a millisecond cold and zero warm: read the Cache Inspector's counters rather than the clock. Watch the walk rather than inferring it:

```bash
dig +trace web.example.com A
```

The Cache Inspector is no longer empty. Every step of the walk is in it — the delegation `NS` records and their glue addresses as well as the `A` record you asked for — each with a TTL counting down.

---

### 2. Warm resolution and TTL decay

Ask the identical question again:

```bash
dig web.example.com A
```

`res1` answered from memory without touching the hierarchy: **Queries** and **Cache Hits** in the Cache Inspector both went up by one, and the **Hit Ratio** climbs with every repeat.

Now watch the `web.example.com. A` row's TTL fall by one each second. When it reaches zero the row disappears, and the next `dig` walks again. The delegations expired with it: this lab caps every positive answer at the same 120 seconds, so the walk starts from the root again. Outside a lab a TLD delegation lives for a day or two while a web server's address may live five minutes, so a real Resolver re-asks only the last step.

From the Lab Prompt in CLI Mode: `show_cache res1`.

---

### 3. Negative caching and flushing

Ask for a name that is not in the Zone:

```bash
dig nosuch.example.com A
dig web.example.com AAAA
```

The first is `NXDOMAIN`; the second is `NOERROR` with the AAAA answer. Look for `nosuch.example.com.` in the Cache Inspector with a yellow **NXDOMAIN** badge: the Resolver cached the *absence* of an answer (RFC 2308), bounded by the Zone's SOA minimum and this lab's 30-second cap.

You do not have to wait out a TTL to force a new walk. Press **Flush** on one row to evict only that name, or **Flush Entire Cache** to clear everything. From the Lab Prompt:

```text
mininet-iplab> flush_cache res1 web.example.com
mininet-iplab> flush_cache res1
```

---

### 4. Authoritative answers, CNAMEs and reverse lookups

Ask `auth1` directly, bypassing the Resolver:

```bash
dig @10.0.3.2 ns1.example.com
```

The header carries `flags: qr aa rd`: `aa` marks an *authoritative* answer, which the Resolver's own answers lack. Then try the pre-configured CNAME and a reverse lookup, which is a `PTR` record in `10.in-addr.arpa.` that `root1` delegates to the nameservers:

```bash
dig alias.example.com
dig -x 10.0.3.2
dig -x 10.0.6.2 +short
```

`alias` returns the `CNAME` to `web.example.com.` and then its address. `10.0.3.2` has three names (`ns1`, `web` and `mail`), so three `PTR` answers come back. Standard tools resolve through `res1` too:

```bash
ping -c 3 web.example.com
dig @2001:db8:5::2 web.example.com AAAA +short
```

The second query reaches the Resolver over IPv6 and the answer carries the IPv6 address of `web`.

---

### 5. Watch the queries and add a Record

Click `auth1` (or its `DNS` badge) to open its DNS modal and keep it up. Its **Live Query Activity** panel counts queries by type and response code. Send a burst from `client1` straight to `auth1`:

```bash
for i in {1..5}; do dig @10.0.3.2 web.example.com A +short; done
for i in {1..3}; do dig @10.0.3.2 missing.example.com A +short; done
```

`NOERROR` and `NXDOMAIN` counts match what you sent; **Reset Statistics** clears them.

With `example.com.` selected press **Add Record** and enter Name `app`, Type `A`, TTL `300`, Target `10.0.3.99`, then **Save Record**. Confirm from `client1`:

```bash
dig @10.0.3.2 app.example.com A +short
```

The record is committed to Knot DNS in one zero-downtime transaction. CLI equivalent: `dns_records auth1 example.com. add app A 10.0.3.99`.

---

### 6. The Secondary: Zone Transfer, Notify and Serial

`auth2` copied both Zones from `auth1` when the lab started. Click `auth2` (its badge reads `DNS · SECONDARY`) and read the **Secondary Replication Header** and the **Transfer Log**:

- **Primary Server**: `auth1`.
- **Last Transfer**: an `AXFR` from `— → 1`, or from the Serial your edit in step 5 reached, with its size in bytes.
- The Records table is **read-only**: a Secondary rejects direct writes. A link in the advisory banner opens the Primary's editor.

From the Lab Prompt: `show_dns_transfers auth2 example.com.`. Check that `auth2` answers authoritatively, not as a cache:

```bash
dig @10.0.4.2 web.example.com A +noall +answer
dig @10.0.4.2 example.com SOA +noall +comments
```

The flags include `aa`. Now add a Record on `auth1` (step 5, or `dns_records auth1 example.com. add brandnew A 10.0.3.100`). Within seconds `auth2`'s Transfer Log gains an `IXFR` row, `Serial` up by one: the Primary's **Notify** prompted the Secondary to ask immediately rather than wait for the 3600-second SOA refresh. An incremental transfer carries only the change, so it is smaller than the first `AXFR` (285 bytes against 463 here; the gap widens with the size of the Zone).

```bash
dig @10.0.4.2 brandnew.example.com A +short
```

Two buttons on the Secondary's modal show the other transfer kinds. **Refresh** asks whether the Serial moved; if not, nothing transfers (`dns_refresh auth2 example.com.`). **Full Transfer** forces another `AXFR` of the same Serial (`dns_retransfer auth2 example.com.`).

---

### 7. Cut the Primary off: SOA expiry, then restore

A Secondary keeps serving its copy while the Primary is away, but only for as long as the Zone's SOA **expire** timer allows. Read this lab's timers from the SOA:

```bash
dig @10.0.4.2 example.com SOA +short
```

The last four numbers are `refresh 3600`, `retry 60`, `expire 600` and `minimum 300`. This is a classroom value; production Zones use days.

1. In the Topology click the link between `auth1` and `r1` and choose **Disconnect Link**.
2. In `auth2`'s modal the replication banner turns to a warning: *Primary unreachable*, with the next retry 60 seconds away.
3. Ask `auth2` for a name from `client1`. It still answers authoritatively:

   ```bash
   dig @10.0.4.2 web.example.com A +noall +comments
   ```

4. Wait ten minutes from the moment the Zone last refreshed. The banner turns red: *Zone expired*. Ask again:

   ```bash
   dig @10.0.4.2 web.example.com A +noall +comments
   ```

   The status is now `SERVFAIL`: with no Primary to confirm it, `auth2` stops answering rather than serve data that may be stale (RFC 1034, RFC 2181).
5. **Restore the Primary.** Click the same link and choose **Reconnect Link**. Disconnecting a Host's only link removes the default routes it had, and Mininet does not put them back when the link returns; the connected route returns alone, so `auth1` could hear `r1` but cannot answer `auth2` across the Router. In `auth1`'s Terminal put the default routes back:

   ```bash
   ip route add default via 10.0.3.1
   ip -6 route add default via 2001:db8:3::1
   ```

   Then press **Refresh** on `auth2` (or run `dns_refresh auth2 example.com.`) and ask once more:

   ```bash
   dig @10.0.4.2 web.example.com A +noall +comments
   ```

   `NOERROR`, with the answer back. Later sections work as before.

`r1` needs no repair of its own: all its routes are connected ones, which return by themselves when the link does.

---

### 8. Access Control on the Resolver

Open the Resolver modal's **Access Control** tab. By default two rules are in force, `0.0.0.0/0 allow` and `::/0 allow`. `client1` is on `10.0.6.0/24`. Add a rule:

- **Client Subnet (CIDR)**: `10.0.6.0/24`
- **Action**: `refuse`

Ask again:

```bash
dig web.example.com A
```

The status is `REFUSED`. The narrower rule won although `0.0.0.0/0 allow` is still listed: Unbound matches by **longest prefix**, not by list order. The cache itself survived (the rule was applied with a reload that keeps cached data). Restore service by deleting the rule, or by editing it to `allow`; the next `dig` answers again. A `deny` rule would instead drop the query so `dig` times out, indistinguishable from a Resolver that is down.

From the Lab Prompt: `resolver_access res1 add 10.0.6.0/24 refuse`, then `resolver_access res1 del 10.0.6.0/24`. Rules you author are Exercise Configuration: they vanish when the Lab stops.

---

### 9. A refused Zone Transfer, and its undo

A Primary decides who may copy a Zone with an ACL listed on the Zone. Take `example.com.`'s ACL away in `auth1`'s Terminal:

```bash
knotc conf-begin
knotc conf-unset 'zone[example.com.].acl'
knotc conf-commit
```

Force a transfer from the Lab Prompt: `dns_retransfer auth2 example.com.`, then `show_dns_transfers auth2 example.com.`. The state is `transfer-refused`, the log row reads `failed: server responded with error 'NOTAUTH'`, and the next retry is a minute away. `auth2` keeps serving its copy meanwhile.

Now undo it in `auth1`'s Terminal, so later sections still work:

```bash
knotc conf-begin
knotc conf-set 'zone[example.com.].acl' transfer_example_com
knotc conf-commit
```

Run `dns_retransfer auth2 example.com.` again and the state is back to `replicated`.

---

### 10. Diagnostic warnings

Mininet-IPLab validates DNS configuration at startup: fatal errors (an invalid domain name) halt the Lab, while teachable misconfigurations warn. If a Zone delegates to a child nameserver without a glue `A` record in the parent (`NSRecord('sub', 'ns.sub.example.com.')`), a startup warning says so, because a Resolver cannot follow a referral it has no address for. This lab's delegations all carry their glue: look at the `com.` delegation in `root1`'s Records.

---

### Things to try next

- Disconnect the `tld1`–`r1` link, flush `res1`, and ask for `web.example.com`: which part of the answer does `res1` still have, and which can it no longer reach? Reconnect it afterwards.
- Compare this lab with `dnssec-lab`: the same arrangement, signed, so the Resolver can prove an answer is genuine.
