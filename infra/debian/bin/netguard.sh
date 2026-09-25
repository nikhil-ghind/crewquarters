#!/bin/sh
# Drop host-bound traffic from Crewquarters agent/model bridges (cqa-*, cqm-*).
# Idempotent: rules are only inserted when absent.
set -eu
ACTION="${1:-apply}"
for tool in iptables ip6tables; do
    command -v "$tool" >/dev/null 2>&1 || continue
    for iface in 'cqa-+' 'cqm-+'; do
        if [ "$ACTION" = apply ]; then
            "$tool" -C INPUT -i "$iface" -j DROP 2>/dev/null || "$tool" -I INPUT 1 -i "$iface" -j DROP
        else
            while "$tool" -C INPUT -i "$iface" -j DROP 2>/dev/null; do
                "$tool" -D INPUT -i "$iface" -j DROP
            done
        fi
    done
done
