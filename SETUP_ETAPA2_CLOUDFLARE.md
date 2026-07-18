# Etapa 2 — Precios live vía 1816, con acceso por email (Cloudflare)

Esta guía despliega **tu versión privada** del monitor: los precios live vienen de 1816 (con
fallback a Eco), la API key queda oculta en el servidor, y **solo emails autorizados** pueden entrar.
El sitio público del colega (GitHub Pages) **no se toca** — esto es un despliegue paralelo tuyo.

**Todo por el dashboard web** (no hace falta Node ni la CLI). Arquitectura:

```
Navegador (email autorizado)
  └─ Cloudflare Access (allowlist de emails)
       └─ Cloudflare Pages (tu fork) ── /api/precios (Pages Function)
                                            ├─ API key = Secret (invisible)
                                            ├─ caché 10 min (acota créditos)
                                            └─ 1816  (fallback: Eco)
```

## 0) Fork del repo
En GitHub, entrá a `Frangranda/Monitor-SoberanosARG` → botón **Fork** → creá
`Ignacio-Talento/Monitor-SoberanosARG`. (El código de esta etapa se sube a tu fork; abajo lo vemos.)

## 1) Cuenta de Cloudflare
Si no tenés, creá una gratis en https://dash.cloudflare.com/sign-up. No hace falta dominio propio:
Cloudflare Pages te da una URL `*.pages.dev`.

## 2) Crear el proyecto en Cloudflare Pages
1. Dashboard → **Workers & Pages** → **Create** → pestaña **Pages** → **Connect to Git**.
2. Autorizá GitHub y elegí tu fork `Monitor-SoberanosARG`.
3. **Build settings:**
   - Framework preset: **None**
   - Build command: **(vacío)**
   - Build output directory: **/** (raíz)
   - (Las Functions en `/functions` se detectan solas.)
4. **Save and Deploy.** Te queda una URL tipo `https://monitor-soberanosarg.pages.dev`.

## 3) Cargar la API key como Secret (la pegás vos)
1. En el proyecto Pages → **Settings** → **Variables and secrets** (o "Environment variables").
2. **Add** una variable, tipo **Secret** (encriptada):
   - Name: `API_1816_KEY`
   - Value: **pegá tu API key de 1816** (la misma del archivo local `.1816_key`).
3. (Temporal, solo para probar antes de Access) agregá otra variable normal:
   - Name: `ALLOW_NO_ACCESS`  Value: `1`   ← **la vas a borrar en el paso 5.**
4. **Save** y redeploy (Deployments → Retry/Redeploy) para que tome las variables.

> La key queda server-side y encriptada: el navegador nunca la ve.

## 4) Probar que trae precios de 1816
1. Abrí `https://<tu-proyecto>.pages.dev/bonos.html`.
2. Cargá `Instrumentos.xlsx` si lo pide y tocá **traer precios**.
3. Deberían poblarse desde 1816. (Chequeo directo del proxy: abrí
   `https://<tu-proyecto>.pages.dev/api/precios?ticker=AL30&grupo=usdbonares` → debe devolver
   `{"AL30": <número>}`.)

## 5) Restringir el acceso por email (Cloudflare Access)
1. Dashboard → **Zero Trust** (si es la primera vez, elegí el plan **Free**, hasta 50 usuarios).
2. **Access** → **Applications** → **Add an application** → **Self-hosted**.
3. **Application domain:** tu dominio de Pages, ej. `monitor-soberanosarg.pages.dev`
   (dejá el path vacío → cubre TODO el sitio, incluido `/api/*`).
4. **Add a policy:**
   - Policy name: `Autorizados`
   - Action: **Allow**
   - Include → **Emails** → agregá los mails permitidos (o **Emails ending in** para un dominio).
5. Guardá la aplicación. Cloudflare va a pedir login (por email/código) a quien entre.
6. **Volvé al paso 3 y BORRÁ la variable `ALLOW_NO_ACCESS`** (y redeploy). Así el `/api/precios`
   queda **fail-closed**: sin pasar por Access, no responde (no se queman créditos).

## 6) Verificación final
- Entrá con un **email de la lista** → te pide login → entrás → el monitor trae precios de 1816. ✅
- Entrá con un email **fuera** de la lista → Access te bloquea. ✅
- (Créditos) Tocá "traer precios" dos veces seguidas: la segunda usa el **caché** (no consume 1816).

## Mantenimiento
- **Actualizar desde el repo del colega:** en tu fork, botón **Sync fork** (o `git pull upstream main`).
  Cloudflare Pages redeploya solo al haber push en la rama de producción.
- **Cambiar la lista de emails:** Zero Trust → Access → Applications → tu app → Policies.
- **Ajustar el caché/créditos:** en `functions/api/precios.js`, constante `CACHE_TTL` (segundos).
- **Rotar la key:** generá una nueva en 1816 y actualizá el Secret `API_1816_KEY` en Pages.

## Notas
- Alcance v1: solo `bonos.html` usa 1816 live. Las páginas `sendero*` siguen con Eco (etapa 3).
- Si el proxy fallara, el frontend cae solo a Eco Valores (no se rompe la vista).
