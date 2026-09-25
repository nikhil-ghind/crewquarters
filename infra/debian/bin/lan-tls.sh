#!/bin/sh
# Device-local certificate authority and HTTPS certificate for LAN HTTPS mode (PLAN.md
# section 10.1; docs/runbooks/lan-https.md). Pure openssl; touches only DIR.
#
#   lan-tls.sh issue DIR HOSTNAME [IP...]   create the CA once, then (re)issue server.crt
#   lan-tls.sh fingerprint DIR              SHA-256 fingerprint of the CA certificate
#   lan-tls.sh names HOSTNAME               the DNS names a certificate for HOSTNAME covers
#   lan-tls.sh primary HOSTNAME             the name to browse to (HOSTNAME.local or HOSTNAME)
#
# DIR gets ca.key (0600), ca.crt, server.key (0600; the caller may widen it to its group),
# server.crt and ca.hostname. The CA is reused while HOSTNAME stays the same, so browsers
# that trust it keep trusting re-issued certificates (new LAN addresses, renewal). A new
# HOSTNAME makes a new CA, because the CA is name-constrained to its host name.
#
# The CA can only vouch for this device: X.509 name constraints limit it to HOSTNAME,
# HOSTNAME.local, localhost and private IPv4 ranges, so installing it on a phone does not
# let it impersonate any other site even if its key leaked.
set -eu
umask 077

CA_DAYS=3650
# Apple platforms reject TLS server certificates valid for more than 825 days, even from
# a user-installed CA. Re-running `crewquarters lan-https enable` renews it.
SERVER_DAYS=825

die() { echo "lan-tls: $*" >&2; exit 2; }

valid_hostname() {
    printf '%s' "$1" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$' \
        && [ "${#1}" -le 253 ]
}

# A single-label name (e.g. "spark") also gets "spark.local", the name mDNS (Avahi)
# announces on the LAN. A dotted name (e.g. "spark.example.com") is used as is.
names() {
    host=$(printf '%s' "$1" | tr 'A-Z' 'a-z')
    echo "$host"
    case "$host" in
        *.*) ;;
        *) echo "$host.local" ;;
    esac
    echo localhost
}

# The name the device is reached by: HOSTNAME.local for a single label, else HOSTNAME.
primary() {
    case "$1" in
        *.*) printf '%s\n' "$1" | tr 'A-Z' 'a-z' ;;
        *) printf '%s.local\n' "$1" | tr 'A-Z' 'a-z' ;;
    esac
}

private_ipv4() {
    printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || return 1
    case "$1" in
        10.*|127.*|192.168.*|169.254.*) return 0 ;;
        172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
        100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
    esac
    return 1
}

issue() {
    dir=$1 host=$2
    shift 2
    valid_hostname "$host" || die "invalid host name: $host"
    for ip in "$@"; do
        private_ipv4 "$ip" || die "not a private IPv4 address: $ip (the CA is limited to LAN ranges)"
    done
    command -v openssl >/dev/null 2>&1 || die "openssl is required"
    mkdir -p "$dir"
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT
    host=$(printf '%s' "$host" | tr 'A-Z' 'a-z')

    if [ ! -s "$dir/ca.key" ] || [ ! -s "$dir/ca.crt" ] \
            || [ "$(cat "$dir/ca.hostname" 2>/dev/null)" != "$host" ]; then
        if [ -s "$dir/ca.crt" ]; then
            echo "lan-tls: host name changed; creating a new device CA (browsers must trust it again)." >&2
        fi
        {
            echo "[req]"
            echo "distinguished_name = dn"
            echo "prompt = no"
            echo "x509_extensions = v3_ca"
            echo "[dn]"
            echo "O = Crewquarters"
            echo "CN = Crewquarters device CA ($host)"
            echo "[v3_ca]"
            echo "basicConstraints = critical,CA:TRUE,pathlen:0"
            echo "keyUsage = critical,keyCertSign,cRLSign"
            echo "subjectKeyIdentifier = hash"
            echo "nameConstraints = critical,@nc"
            echo "[nc]"
            i=0
            for name in $(names "$host"); do
                echo "permitted;DNS.$i = $name"
                i=$((i + 1))
            done
            echo "permitted;IP.0 = 10.0.0.0/255.0.0.0"
            echo "permitted;IP.1 = 172.16.0.0/255.240.0.0"
            echo "permitted;IP.2 = 192.168.0.0/255.255.0.0"
            echo "permitted;IP.3 = 127.0.0.0/255.0.0.0"
            echo "permitted;IP.4 = 169.254.0.0/255.255.0.0"
            echo "permitted;IP.5 = 100.64.0.0/255.192.0.0"
        } > "$tmp/ca.cnf"
        openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/ca.key" 2>/dev/null
        openssl req -x509 -new -key "$tmp/ca.key" -config "$tmp/ca.cnf" -sha256 \
            -days "$CA_DAYS" -set_serial "0x$(openssl rand -hex 16)" -out "$tmp/ca.crt"
        mv "$tmp/ca.key" "$dir/ca.key"
        mv "$tmp/ca.crt" "$dir/ca.crt"
        printf '%s\n' "$host" > "$dir/ca.hostname"
    fi

    {
        echo "[req]"
        echo "distinguished_name = dn"
        echo "prompt = no"
        echo "[dn]"
        echo "O = Crewquarters"
        echo "CN = $(primary "$host")"
        echo "[v3_srv]"
        echo "basicConstraints = critical,CA:FALSE"
        echo "keyUsage = critical,digitalSignature"
        echo "extendedKeyUsage = serverAuth"
        echo "subjectKeyIdentifier = hash"
        echo "authorityKeyIdentifier = keyid,issuer"
        echo "subjectAltName = @san"
        echo "[san]"
        i=0
        for name in $(names "$host"); do
            echo "DNS.$i = $name"
            i=$((i + 1))
        done
        i=0
        for ip in 127.0.0.1 "$@"; do
            echo "IP.$i = $ip"
            i=$((i + 1))
        done
    } > "$tmp/server.cnf"
    openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/server.key" 2>/dev/null
    openssl req -new -key "$tmp/server.key" -config "$tmp/server.cnf" -out "$tmp/server.csr"
    openssl x509 -req -in "$tmp/server.csr" -CA "$dir/ca.crt" -CAkey "$dir/ca.key" \
        -set_serial "0x$(openssl rand -hex 16)" -days "$SERVER_DAYS" -sha256 \
        -extfile "$tmp/server.cnf" -extensions v3_srv -out "$tmp/server.crt" 2>/dev/null
    openssl verify -CAfile "$dir/ca.crt" "$tmp/server.crt" >/dev/null \
        || die "the new certificate does not verify against the device CA"
    mv "$tmp/server.key" "$dir/server.key"
    mv "$tmp/server.crt" "$dir/server.crt"
    chmod 0600 "$dir/ca.key" "$dir/server.key"
    chmod 0644 "$dir/ca.crt" "$dir/server.crt" "$dir/ca.hostname"
}

fingerprint() {
    openssl x509 -in "$1/ca.crt" -noout -fingerprint -sha256 | sed 's/^.*=//'
}

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
    issue) [ $# -ge 2 ] || die "usage: lan-tls.sh issue DIR HOSTNAME [IP...]"; issue "$@" ;;
    fingerprint) [ $# -eq 1 ] || die "usage: lan-tls.sh fingerprint DIR"; fingerprint "$1" ;;
    names) [ $# -eq 1 ] || die "usage: lan-tls.sh names HOSTNAME"; names "$1" ;;
    primary) [ $# -eq 1 ] || die "usage: lan-tls.sh primary HOSTNAME"; primary "$1" ;;
    *) die "usage: lan-tls.sh issue|fingerprint|names|primary ..." ;;
esac
