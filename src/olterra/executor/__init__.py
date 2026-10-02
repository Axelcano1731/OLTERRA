"""Ejecutor genérico: abre sesiones SSH y hace SNMP walk según los planes que recibe.

No sabe de modelos ni de comandos de VSOL: eso vive en los drivers, en la nube.
Recibe planes, devuelve salida cruda. Así, corregir un parser o sumar un modelo
nunca obliga a actualizar el ejecutor donde el ISP.
"""
