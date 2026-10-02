"""Conciliación OLT ↔ MikroTik ↔ CRM.

El motor es una función pura sobre cuatro listas (ONUs, secretos PPPoE, sesiones
activas, clientes del CRM). Las fuentes (CSV, export de RouterOS, API de
RouterOS, API de socios de ISPWatch, capturas de la OLT) solo llenan esas listas.
"""
