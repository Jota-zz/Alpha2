"""Cliente de Anthropic AI (migrado de la celda 8 del notebook).

`AnthropicAIClient` genera respuestas conversacionales aplicando el pipeline de
contexto (base + región + estado + historial) y las reglas de estilo del mensaje
de salida (minúsculas, typos plausibles, concisión). `AnthropicExtractionClient`
extiende el cliente para extraer cotizaciones desde texto o imagen como fallback
del extractor determinista.
"""
from __future__ import annotations

import base64
import unicodedata
import json
import random
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import anthropic

from app.core.config import AnthropicConfig
from app.core.logging import get_logger
from app.utils.text import retry_on_failure, sanitize_user_input

logger = get_logger(__name__)

# Vecinos de teclado QWERTY para errores de dedo plausibles
_TYPO_NEIGHBORS = {
    'a': ['s', 'q'], 'e': ['r', 'w'], 'i': ['o', 'u'], 'o': ['i', 'p'],
    'u': ['i', 'y'], 's': ['a', 'd'], 'n': ['m', 'b'], 'm': ['n'],
    'r': ['t', 'e'], 't': ['r', 'y'], 'l': ['k'], 'c': ['v', 'x'],
}

# Muletillas a recortar para hacer el mensaje conciso
_FILLERS_RE = [
    re.compile(r'\bpor favor\b', re.IGNORECASE),
    re.compile(r'\bme podri[ai]s?\b', re.IGNORECASE),
    re.compile(r'\bsi es posible\b', re.IGNORECASE),
    re.compile(r'\bsi me hace el favor\b', re.IGNORECASE),
    re.compile(r'\bquisiera saber\b', re.IGNORECASE),
    re.compile(r'\bme gustaria saber\b', re.IGNORECASE),
    re.compile(r'\bquisiera consultar\b', re.IGNORECASE),
    re.compile(r'\bnecesitaria\b', re.IGNORECASE),
    re.compile(r'\bdisculpe la molestia\b', re.IGNORECASE),
    re.compile(r'\bno se si\b', re.IGNORECASE),
    re.compile(r'\bla verdad es que\b', re.IGNORECASE),
    re.compile(r'\bestaba pensando que\b', re.IGNORECASE),
]

class AnthropicAIClient:
    """Cliente Anthropic Claude con composición de contexto dinámico."""

    def __init__(self, config: AnthropicConfig, xml_path: Optional[str] = None):
        self.config = config
        self.config.validate()
        self.client = anthropic.Anthropic(api_key=config.api_key)
        self.prompts: Dict[str, Any] = self._default_prompts()

        if xml_path:
            # ✅ FIX 2.11: si el caller pasó un path explícito, fallar ruidosamente
            # si el XML no se puede cargar. Antes caía silenciosamente a defaults
            # con un warning que nadie veía.
            try:
                self.prompts = self._load_xml_prompts(xml_path)
                logger.info(f"✅ XML de prompts cargado: {xml_path}")
                logger.info(f"   Regiones: {list(self.prompts['region'].keys())}")
                logger.info(f"   Estados: {list(self.prompts['estado'].keys())}")
            except FileNotFoundError as e:
                raise RuntimeError(
                    f"❌ XML de prompts no encontrado en {xml_path}. "
                    f"Si quieres usar prompts por defecto, NO pases xml_path."
                ) from e
            except ET.ParseError as e:
                raise RuntimeError(
                    f"❌ XML de prompts malformado en {xml_path}: {e}"
                ) from e

    # ------------------------------------------------------------------
    # CARGA DEL XML (BASE + REGION + ESTADO)
    # ------------------------------------------------------------------
    @staticmethod
    def _xml_block_to_text(elem) -> str:
        """Serializa un nodo XML como [TAG]\\ncontenido para cada hijo."""
        parts = []
        for child in elem:
            tag = child.tag.upper()
            inner = ''.join(child.itertext()).strip()
            if inner:
                parts.append(f"[{tag}]\n{inner}")
        return "\n\n".join(parts)

    def _load_xml_prompts(self, xml_path: str) -> Dict[str, Any]:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        prompts = {'base': '', 'region': {}, 'estado': {}}

        bp = root.find('base_prompt')
        if bp is not None:
            prompts['base'] = self._xml_block_to_text(bp)

        for r in root.findall('.//region'):
            name = (r.get('name') or '').upper()
            if name:
                prompts['region'][name] = self._xml_block_to_text(r)

        for s in root.findall('.//state'):
            name = (s.get('name') or '').lower()
            if name:
                prompts['estado'][name] = self._xml_block_to_text(s)

        if not prompts['base']:
            prompts['base'] = self._default_prompts()['base']

        return prompts

    def _default_prompts(self) -> Dict[str, Any]:
        return {
            'base': (
                "Eres el encargado de compras de la constructora MR. "
                "Hablas como colombiano real, no como IA. "
                "Mensajes cortos tipo whatsapp."
            ),
            'region': {},
            'estado': {},
        }

    # ------------------------------------------------------------------
    # COMBINACIÓN -> CONTEXTO DINÁMICO -> PROMPT FINAL
    # ------------------------------------------------------------------
    def _build_dynamic_context(self, region: str, estado: str,
                                historial: Optional[List[Dict[str, str]]] = None) -> str:
        """
        Combina BASE + REGION + ESTADO + HISTORIAL en un solo system prompt.
        """
        partes = []

        base = self.prompts.get('base', '').strip()
        if base:
            partes.append("===== BASE =====\n" + base)

        p_region = self.prompts.get('region', {}).get((region or '').upper(), '').strip()
        if p_region:
            partes.append(f"===== REGION ({region.upper()}) =====\n" + p_region)

        p_estado = self.prompts.get('estado', {}).get((estado or '').lower(), '').strip()
        if p_estado:
            partes.append(f"===== ESTADO ({estado.lower()}) =====\n" + p_estado)

        if historial:
            lineas = []
            for m in historial:
                # Roles correctos: el bot es la constructora MR (yo, comprador);
                # la ferretería (vendedor) es el "Cliente" en el sentido de
                # interlocutor del bot.
                rol = "Ferretería" if m.get("role") == "user" else "Yo (MR)"
                contenido = (m.get("content") or "").strip()
                if contenido:
                    lineas.append(f"{rol}: {contenido}")
            if lineas:
                partes.append(
                    "===== HISTORIAL DE CONVERSACIÓN =====\n"
                    "Lo siguiente es lo que ya hemos hablado con esta ferretería. "
                    "No vuelvas a preguntar datos que ya están confirmados aquí. "
                    "Si la ferretería pregunta '¿qué hemos hablado?' o "
                    "'¿qué no debes volver a preguntar?', resúmelo a partir de esto:\n"
                    + "\n".join(lineas)
                )

        # Refuerzo del estilo de salida (instrucciones para Claude)
        partes.append(
            "===== ESTILO DE SALIDA OBLIGATORIO =====\n"
            "- Responde como mensaje corto de whatsapp.\n"
            "- Una sola idea por mensaje.\n"
            "- No uses tildes ni emojis.\n"
            "- No expliques quién eres ni que eres una IA.\n"
            "- No saludes en cada turno, solo si recién inicia la conversación.\n"
            "- Devuelve solo el texto del mensaje y usa ?"
        )

        return "\n\n".join(partes)

    # ------------------------------------------------------------------
    # MENSAJE OUTPUT: post-procesado con las 4 reglas
    # ------------------------------------------------------------------
    @staticmethod
    def _strip_accents(text: str) -> str:
        return ''.join(
            c for c in unicodedata.normalize('NFD', text)
            if unicodedata.category(c) != 'Mn'
        )

    def _inject_typos(self, text: str, rate: Optional[float] = None,
                      seed: Optional[int] = None) -> str:
        """
        ✅ FIX 2.10: errores de dedo casuales, pero NO en la última letra
        si el texto termina en '?'. Esto evita outputs feos como
        "cuanto cuesa?" donde la 't' final desaparece.
        """
        if rate is None:
            rate = self.config.typo_rate
        rng = random.Random(seed)

        # Detectar si el texto termina en '?' y proteger la última letra
        ends_with_q = text.rstrip().endswith('?')
        # Índice de la última letra alfabética antes del '?' (o del fin)
        last_alpha_idx = -1
        for i in range(len(text) - 1, -1, -1):
            if text[i].isalpha():
                last_alpha_idx = i
                break

        out = []
        for i, ch in enumerate(text):
            # Proteger la última letra antes del '?' final
            protected = (ends_with_q and i == last_alpha_idx)
            if ch.isalpha() and not protected and rng.random() < rate:
                roll = rng.random()
                low = ch.lower()
                if roll < 0.5 and low in _TYPO_NEIGHBORS:
                    repl = rng.choice(_TYPO_NEIGHBORS[low])
                    out.append(repl.upper() if ch.isupper() else repl)
                elif roll < 0.8:
                    out.append(ch)
                    out.append(ch)  # duplicada
                else:
                    continue  # omitida
            else:
                out.append(ch)
        return ''.join(out)

    @staticmethod
    def _make_concise(text: str) -> str:
        for pat in _FILLERS_RE:
            text = pat.sub('', text)
        return re.sub(r'\s+', ' ', text).strip()

    def _format_output(self, text: str) -> str:
        """
        Aplica las 4 reglas al mensaje final:
        1) solo primera letra mayúscula
        2) errores de dedo casuales (no en última letra antes de '?')
        3) solo `?` permitido y solo si es necesario
        4) conciso
        """
        if not text:
            return ""

        # 0) sin tildes
        text = self._strip_accents(text)

        # 1) eliminar todos los signos excepto '?'
        text = text.replace('¿', '').replace('¡', '')
        text = re.sub(r"[.,;:!\"'`()\[\]{}\-—–/\\*_~|]", ' ', text)

        # 2) normalizar `?`: máximo uno al final, sin espacios previos
        text = re.sub(r'\?+', '?', text)
        text = re.sub(r'\s+\?', '?', text)
        text = text.lstrip('?').strip()

        # 3) colapsar espacios
        text = re.sub(r'\s+', ' ', text).strip()

        # 4) conciso (elimina muletillas)
        text = self._make_concise(text)

        # 4b) re-normalizar '?': la eliminación de muletillas puede haber
        # dejado espacio antes del signo (p.ej. "el precio , por favor ?" →
        # "el precio  ?"). Volver a colapsar contra el '?'.
        text = re.sub(r'\s+\?', '?', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # 5) solo primera letra mayúscula
        text = text.lower()
        if text:
            text = text[0].upper() + text[1:]

        # 6) errores de dedo (al final, sobre el texto ya normalizado)
        text = self._inject_typos(text)

        return text

    # ------------------------------------------------------------------
    # ENTRADA PRINCIPAL
    # ------------------------------------------------------------------
    @retry_on_failure(max_retries=2)
    def get_response(self, user_message: str, region: str = "CENTRO",
                     estado: str = "inicio",
                     historial: Optional[List[Dict[str, str]]] = None) -> str:
        """
        El historial se usa de DOS formas complementarias:
          1) Como resumen textual dentro del system prompt
             (vía _build_dynamic_context)
          2) Como mensajes estructurados antes del mensaje actual
        """
        safe_message = sanitize_user_input(user_message)
        if not safe_message:
            return "Perdon no entendi"

        hist = historial or []
        if self.config.history_limit and len(hist) > self.config.history_limit * 2:
            hist = hist[-self.config.history_limit * 2:]

        system_prompt = self._build_dynamic_context(region, estado, hist)

        mensajes = list(hist) + [{"role": "user", "content": safe_message}]

        try:
            logger.info(
                f"🤖 Claude → región={region}, estado={estado}, "
                f"modelo={self.config.model_name}, "
                f"historial={len(hist)} mensajes"
            )
            response = self.client.messages.create(
                model=self.config.model_name,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=mensajes,
            )
            raw = response.content[0].text.strip()
            logger.info(
                f"✅ tokens in={response.usage.input_tokens} "
                f"out={response.usage.output_tokens}"
            )
            return self._format_output(raw)
        except anthropic.APIError as e:
            logger.error(f"Error API Anthropic: {e}")
            return "Perdon estoy con problemas"
        except Exception as e:
            logger.error(f"Error inesperado: {e}")
            return "Perdon no entendi"

    # ------------------------------------------------------------------
    # ✅ v1.3: generación del parámetro {{1}} de la plantilla "saludo".
    # ------------------------------------------------------------------
    def generate_outreach_param(self,
                                productos_disponibles: Optional[List[str]] = None,
                                region: Optional[str] = None) -> str:
        """
        Genera el contenido CORTO que rellena {{1}} de la plantilla "saludo":

            "Hola, buen día. Quisiera consultar si tienen disponibilidad de:
             {{1}}. Además, ¿me podrían confirmar el precio? Quedo atento,
             muchas gracias."

        El resultado debe ser una frase MUY breve mencionando 2–4 productos
        de cemento del catálogo (ej. "cemento argos o portland"). NO debe
        repetir el saludo ni el cierre — esos ya los provee la plantilla.

        Args:
          productos_disponibles: lista de nombres de producto del catálogo BD.
            Si está vacía/None, se cae a un fallback seguro.
          region: opcional, solo informativo (no se usa para filtrar aquí).

        Devuelve un string sin saltos de línea, sin signos de puntuación al final,
        listo para inyectar como parámetro de plantilla.
        """
        # Fallback determinístico si no hay catálogo o si Claude falla.
        # Lo dejamos genérico (cualquier plaza colombiana lo entiende).
        FALLBACK = "cemento gris en presentación de 50 kg"

        productos_norm: List[str] = []
        for p in (productos_disponibles or []):
            if p and isinstance(p, str):
                productos_norm.append(p.strip())
        # Limitar el catálogo a un nº razonable de opciones para que Claude no
        # se vaya por las ramas y para abaratar tokens.
        productos_norm = productos_norm[:30]

        if not productos_norm:
            logger.info("generate_outreach_param: catálogo vacío → usando fallback")
            return FALLBACK

        # Prompt: instrucciones MUY constreñidas; queremos un fragmento, no
        # un mensaje. La plantilla ya pone el saludo y el cierre.
        system = (
            "Eres un comprador colombiano que arma una frase corta mencionando "
            "2 a 4 marcas o tipos de cemento de un catálogo. La frase irá "
            "insertada DENTRO de un mensaje pre-escrito justo después de "
            "\"disponibilidad de:\" y antes de \". Además, ¿me podrían…\". "
            "Reglas estrictas:\n"
            "- Devuelve SOLO la frase a insertar, nada más.\n"
            "- 3 a 12 palabras. Sin tildes, sin emojis, sin signos finales.\n"
            "- No saludes, no te despidas, no expliques nada.\n"
            "- Usa minúsculas excepto nombres propios de marca.\n"
            "- Conecta con \"o\" o con comas (ej. \"cemento argos o portland\").\n"
            "- Si en el catálogo hay marca y tipo, prioriza marcas reconocibles."
        )
        user = (
            "Catálogo disponible (escoge 2 a 4): "
            + " | ".join(productos_norm)
        )

        try:
            resp = self.client.messages.create(
                model=self.config.model_name,
                max_tokens=self.config.outreach_param_max_tokens,
                temperature=0.4,  # más bajo que el chat: queremos consistencia
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            raw = (resp.content[0].text or "").strip()
            # Saneamiento: quitar comillas envolventes, puntos finales, saltos.
            raw = raw.strip().strip('"').strip("'").strip()
            raw = raw.replace("\n", " ").replace("\r", " ")
            # Colapsar espacios.
            raw = re.sub(r"\s+", " ", raw)
            # Quitar punto/coma final que rompería la lectura ("…: cemento argos.")
            raw = raw.rstrip(".,:;")
            # Validación mínima: longitud razonable y no vacío.
            if not raw or len(raw) < 3 or len(raw) > 200:
                logger.warning(
                    f"generate_outreach_param: salida fuera de rango "
                    f"(len={len(raw)}); usando fallback"
                )
                return FALLBACK
            logger.info(f"🧩 outreach_param generado: {raw!r}")
            return raw
        except Exception as e:
            logger.error(f"generate_outreach_param falló: {e}; usando fallback")
            return FALLBACK

class AnthropicExtractionClient(AnthropicAIClient):
    """
    Cliente especializado en EXTRACCIÓN de señales del mensaje de la ferretería.

    ✅ FIX 2.9: el system prompt y los nombres de variables ahora reflejan
    correctamente los roles:
      - LA FERRETERÍA es la que vende cemento y emite los precios
      - EL BOT (constructora MR) es el comprador, hace preguntas y agradece

    NO post-procesa el mensaje (no aplica las reglas de estilo de WhatsApp).
    """

    def __init__(self, config: AnthropicConfig):
        # super().__init__ no debe cargar XML aquí: el extractor usa su propio prompt
        super().__init__(config, xml_path=None)
        self.extraction_prompt = (
            "Eres un experto en extracción de datos de cotizaciones de ferretería en Colombia.\n\n"
            "CONTEXTO DE LA CONVERSACIÓN:\n"
            "- LA FERRETERÍA es la que VENDE cemento (y otros productos).\n"
            "- EL BOT (constructora MR) es el COMPRADOR que pregunta precios.\n"
            "- Vas a recibir DOS textos: lo que escribió la FERRETERÍA y lo que respondió el BOT.\n"
            "- Tu trabajo es extraer la información comercial que la FERRETERÍA aportó.\n\n"
            "Devuelve un JSON ESTRICTO con estas claves exactas:\n"
            "{\n"
            '  "producto": str|null,           // ej: "cemento", "varilla 1/2", "arena"\n'
            '  "marca": str|null,              // ej: "argos", "cemex", "diamante"\n'
            '  "cantidad": float|null,\n'
            '  "unidad": str|null,             // "bulto", "kg", "m", "ton", "saco"\n'
            '  "precio_unitario": float|null,  // en COP, sin separadores\n'
            '  "disponibilidad": str|null,     // "disponible", "agotado", "pedido"\n'
            '  "observaciones": str|null,\n'
            '  "es_despedida": bool,           // true si la FERRETERÍA cierra la conversación\n'
            '  "confirma_cierre": bool,        // true si la FERRETERÍA va a enviar la cotización formal o confirma el pedido\n'
            '  "confianza": float              // 0.0 a 1.0\n'
            "}\n\n"
            "REGLAS:\n"
            "- Devuelve SOLO JSON válido, sin markdown, sin texto antes ni después.\n"
            "- Usa null cuando el dato no esté presente. NO inventes.\n"
            "- 'es_despedida' es true si el mensaje de la FERRETERÍA termina la conversación: "
            "'gracias', 'hasta luego', 'no necesito nada más', 'no me interesa', 'ya no'. "
            "Un simple 'gracias' en medio de una negociación NO es despedida.\n"
            "- 'confirma_cierre' es true cuando la FERRETERÍA confirma que enviará la "
            "cotización formal o que procede con el pedido. Frases típicas: "
            "'ya le mando la cotización', 'le envío el pdf', 'listo le confirmo el pedido', "
            "'mando la propuesta', 'paso la cotización', 'hago la orden'. "
            "NO es true por simplemente dar un precio; debe haber compromiso explícito de "
            "enviar documento formal o cerrar la venta.\n"
            "- 'precio_unitario' debe ser número, no string. Convierte '$32.000' → 32000.\n"
            "- Si la FERRETERÍA solo saluda o pregunta cosas sin dar datos, todos los campos "
            "de cotización van en null y confianza baja (<0.3)."
        )

    @retry_on_failure(max_retries=3)
    def extract_quote_info(self, mensaje_ferreteria: str, respuesta_bot: str,
                           interaction_id: str,
                           historial: Optional[List[Dict[str, str]]] = None) -> Optional[Dict]:
        """
        Extrae señales del último intercambio.

        ✅ FIX 2.9: nombres de parámetros y del payload ahora reflejan roles:
        `mensaje_ferreteria` (lo que escribió la ferretería que vende) y
        `respuesta_bot` (lo que respondió el bot comprador).
        `interaction_id` se acepta por compatibilidad pero la persistencia
        la hace MessageProcessor con el DatabaseManager.
        """
        safe_ferreteria = sanitize_user_input(mensaje_ferreteria)
        safe_bot = sanitize_user_input(respuesta_bot, max_length=2000)
        try:
            logger.info("🤖 Extrayendo señales del mensaje...")
            # ✅ NUEVO: si llega historial, lo incluimos como contexto
            # para que `es_despedida` y `confirma_cierre` se evalúen sobre la
            # CONVERSACIÓN, no solo el último turno aislado.
            contexto_historial = ""
            if historial:
                ult_mensajes = []
                for h in historial[-8:]:
                    rol = "FERRETERÍA" if h.get("role") == "user" else "BOT"
                    contenido = (h.get("content") or "").strip()
                    if not contenido or contenido.startswith("[OUTREACH"):
                        continue
                    ult_mensajes.append(f"{rol}: {contenido}")
                if ult_mensajes:
                    contexto_historial = (
                        "CONTEXTO DE LA CONVERSACIÓN PREVIA (más antigua arriba):\n"
                        + "\n".join(ult_mensajes) + "\n\n"
                    )

            response = self.client.messages.create(
                model=self.config.model_name,
                max_tokens=self.config.max_tokens * 3,
                temperature=0.1,
                system=self.extraction_prompt,
                messages=[{
                    "role": "user",
                    "content": (
                        f"{contexto_historial}"
                        f"ÚLTIMO INTERCAMBIO:\n"
                        f"Mensaje de la FERRETERÍA (vendedor): {safe_ferreteria}\n\n"
                        f"Respuesta del BOT (comprador MR): {safe_bot}"
                    ),
                }],
            )
            text = response.content[0].text.strip()
            for fence in ('```json', '```'):
                if text.startswith(fence):
                    text = text[len(fence):].lstrip()
            if text.endswith('```'):
                text = text[:-3].rstrip()
            data = json.loads(text)
            data.setdefault("producto", None)
            data.setdefault("marca", None)
            data.setdefault("precio_unitario", None)
            data.setdefault("disponibilidad", None)
            data.setdefault("observaciones", None)
            data.setdefault("es_despedida", False)
            data.setdefault("confirma_cierre", False)
            data.setdefault("confianza", 0.0)
            logger.info(
                f"✅ Extracción → producto={data.get('producto')}, "
                f"marca={data.get('marca')}, precio={data.get('precio_unitario')}, "
                f"despedida={data.get('es_despedida')}, "
                f"cierre={data.get('confirma_cierre')}, "
                f"confianza={data.get('confianza')}"
            )
            return data
        except json.JSONDecodeError as e:
            logger.error(f"JSON inválido del extractor: {e}")
            return None
        except Exception as e:
            logger.error(f"Error en extracción: {e}")
            return None

    @staticmethod
    def tiene_cotizacion_completa(data: Dict) -> bool:
        """Determina si la extracción aporta precio + marca + producto usables."""
        if not data:
            return False
        precio = data.get("precio_unitario")
        marca = (data.get("marca") or "").strip()
        producto = (data.get("producto") or "").strip()
        try:
            precio_ok = precio is not None and float(precio) > 0
        except (TypeError, ValueError):
            precio_ok = False
        return precio_ok and bool(marca) and bool(producto)

    @staticmethod
    def tiene_confirmacion_cierre(data: Dict) -> bool:
        """True si la ferretería confirma cierre / envío de cotización formal por texto."""
        if not data:
            return False
        return bool(data.get("confirma_cierre"))

    # ------------------------------------------------------------------
    # PROMPT COMPARTIDO entre PDF e imagen para extraer líneas de cemento.
    # Lo separamos para que ambos flujos sean exactamente equivalentes.
    # ------------------------------------------------------------------
    _CEMENTO_EXTRACTION_PROMPT = (
        "Eres un experto en extracción de cotizaciones de ferretería. "
        "Recibirás una cotización (PDF o imagen) enviada por una ferretería "
        "colombiana. Tu tarea es localizar TODAS las líneas del producto "
        "CEMENTO (cualquier presentación: gris, blanco, portland, "
        "estructural, etc.) e ignorar TODOS los demás productos.\n\n"
        "Devuelve un JSON ESTRICTO con esta forma exacta:\n"
        "{\n"
        '  "lineas_cemento": [\n'
        "    {\n"
        '      "producto": str|null,\n'
        '      "marca": str|null,\n'
        '      "cantidad": float|null,\n'
        '      "unidad": str|null,             // "bulto", "saco", "kg", "ton"\n'
        '      "precio_unitario": float|null,  // COP, sin separadores\n'
        '      "disponibilidad": str|null,\n'
        '      "observaciones": str|null,      // notas solo de esta línea\n'
        '      "confianza": float              // 0.0 a 1.0 para esta línea\n'
        "    }\n"
        "  ],\n"
        '  "confianza_global": float           // qué tan confiado estás del documento en general\n'
        "}\n\n"
        "REGLAS CRÍTICAS:\n"
        "- Devuelve SOLO JSON válido, sin markdown ni texto adicional.\n"
        "- Incluye TODAS las líneas de cemento. Si hay 5 líneas (distintas marcas/"
        "presentaciones/precios), devuelve 5 entradas en `lineas_cemento`.\n"
        "- NO deduplicar: si el documento tiene la misma línea repetida, "
        "devuélvela repetida. La fidelidad manda.\n"
        "- Si el documento NO contiene cemento, devuelve `lineas_cemento: []` "
        "y `confianza_global < 0.3`.\n"
        "- 'precio_unitario' debe ser número, no string. '$32.000' → 32000.\n"
        "- Cada línea es independiente: NO infieras valores entre filas. "
        "Si una línea no tiene marca explícita pero otra sí, esa marca NO "
        "se copia a la primera.\n"
        "- Para imágenes de baja calidad o difíciles de leer, baja la "
        "confianza por línea pero igual extrae lo que veas."
    )

    def _parse_cemento_response(self, text: str) -> Optional[List[Dict]]:
        """Parsea la respuesta del extractor de cemento (común a PDF e imagen)."""
        for fence in ('```json', '```'):
            if text.startswith(fence):
                text = text[len(fence):].lstrip()
        if text.endswith('```'):
            text = text[:-3].rstrip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON inválido en extracción de cemento: {e}")
            return None

        if isinstance(payload, list):
            lineas_raw = payload
            conf_global = None
        else:
            lineas_raw = payload.get("lineas_cemento", []) or []
            conf_global = payload.get("confianza_global")

        if not isinstance(lineas_raw, list):
            logger.warning("Extracción cemento: `lineas_cemento` no es lista, devuelvo []")
            return []

        lineas: List[Dict] = []
        for item in lineas_raw:
            if not isinstance(item, dict):
                continue
            item.setdefault("producto", None)
            item.setdefault("marca", None)
            item.setdefault("cantidad", None)
            item.setdefault("unidad", None)
            item.setdefault("precio_unitario", None)
            item.setdefault("disponibilidad", None)
            item.setdefault("observaciones", None)
            item.setdefault("es_despedida", False)
            item.setdefault("confianza", 0.0)
            lineas.append(item)

        logger.info(
            f"📄 Extracción → {len(lineas)} línea(s) de cemento "
            f"(confianza_global={conf_global})"
        )
        for i, l in enumerate(lineas, 1):
            logger.info(
                f"   [{i}/{len(lineas)}] producto={l.get('producto')}, "
                f"marca={l.get('marca')}, precio={l.get('precio_unitario')}, "
                f"confianza={l.get('confianza')}"
            )
        return lineas

    @retry_on_failure(max_retries=3)
    def extract_quote_from_pdf(self, pdf_bytes: bytes) -> Optional[List[Dict]]:
        """
        Extrae TODAS las líneas de CEMENTO desde un PDF de cotización.

        Devuelve:
          - List[Dict] (puede ser []) en caso de éxito.
          - None solo si falla la API o el JSON no se pudo parsear.
        """
        import base64
        if not pdf_bytes:
            return None
        try:
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
            response = self.client.messages.create(
                model=self.config.model_name,
                max_tokens=self.config.max_tokens * 8,
                temperature=0.1,
                system=self._CEMENTO_EXTRACTION_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Extrae TODAS las líneas de cemento del PDF "
                                "siguiendo las reglas del system prompt. Si hay "
                                "varias, todas; si no hay ninguna, lista vacía."
                            ),
                        },
                    ],
                }],
            )
            return self._parse_cemento_response(response.content[0].text.strip())
        except Exception as e:
            logger.error(f"Error en extract_quote_from_pdf: {e}")
            return None

    @retry_on_failure(max_retries=3)
    def extract_quote_from_image(self, image_bytes: bytes,
                                 mime_type: str = "image/jpeg") -> Optional[List[Dict]]:
        """
        ✅ NUEVO: extrae líneas de CEMENTO desde una IMAGEN (foto de cotización).

        Soporta los formatos que acepta la API de Anthropic:
        image/jpeg, image/png, image/gif, image/webp.

        Mismo contrato de retorno que extract_quote_from_pdf:
          - List[Dict] (puede ser []) en caso de éxito.
          - None si falla la API o el JSON no se pudo parsear.
        """
        import base64
        if not image_bytes:
            return None

        # Validar mime soportado
        SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}
        mime_clean = (mime_type or "").lower().split(";")[0].strip()
        if mime_clean not in SUPPORTED:
            logger.warning(
                f"Imagen con MIME no soportado por Claude vision: {mime_clean}. "
                f"Soportados: {SUPPORTED}"
            )
            return None

        try:
            img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
            response = self.client.messages.create(
                model=self.config.model_name,
                max_tokens=self.config.max_tokens * 8,
                temperature=0.1,
                system=self._CEMENTO_EXTRACTION_PROMPT,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_clean,
                                "data": img_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Esta es una foto de una cotización enviada por la "
                                "ferretería. Extrae TODAS las líneas de cemento "
                                "siguiendo las reglas del system prompt. Si la "
                                "imagen no contiene cemento (es otra cosa: factura "
                                "general, foto del local, etc.) devuelve "
                                "`lineas_cemento: []` con confianza_global baja."
                            ),
                        },
                    ],
                }],
            )
            return self._parse_cemento_response(response.content[0].text.strip())
        except Exception as e:
            logger.error(f"Error en extract_quote_from_image: {e}")
            return None

__all__ = ["AnthropicAIClient", "AnthropicExtractionClient"]
