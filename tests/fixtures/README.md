# Capturas de laboratorio

Aquí van las capturas de `olterra-capture` **ya revisadas**, con esta forma:

```
tests/fixtures/<driver>/<modelo>/<firmware>/<fecha>/manifest.json
tests/fixtures/<driver>/<modelo>/<firmware>/<fecha>/cli/*.txt
tests/fixtures/<driver>/<modelo>/<firmware>/<fecha>/snmp/*.snmprec
```

`tests/unit/test_fixtures.py` las recorre solo: cada salida con parser tiene que
reconocerse. Antes de copiar una captura aquí, ver el procedimiento de revisión en
`docs/LABORATORIO.md` (anonimizar, revisar descripciones y nombres de clientes).
