"""DAV-Interop: iCalendar/vCard erzeugen + parsen und externe CalDAV/CardDAV-
Konten als read-only Quelle anbinden (Client-Proxy, Variante B aus KONZEPT.md).

Bewusst ohne schwere Fremd-Bibliothek: die Formate (RFC 5545 iCalendar,
RFC 6350 vCard) sind textbasiert und werden hier mit einem kleinen, getesteten
Encoder/Decoder bedient. Das hält das Single-Container-Image schlank.
"""
