"""
Bot Discord — Traduction Turc → Français
-----------------------------------------
Préfixe des commandes : *

Commandes :
  *traduire <texte>   (alias *tr)  → traduit un texte turc en français
  *auto                            → active/désactive l'auto-traduction dans le salon
  *aide                (alias *help)→ affiche l'aide

Bonus :
  Réagis avec 🇹🇷 sur un message    → le bot le traduit en français
"""

import os
import json
import re
import discord
from discord.ext import commands
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# Charge le fichier .env s'il existe (pratique en local).
# Sans python-dotenv installé, on ignore simplement cette étape.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────

TOKEN = os.environ.get("DISCORD_TOKEN")
PREFIX = "*"
TRIGGER_EMOJI = "🇹🇷"

# ID du propriétaire du bot (en dur) : seul cet utilisateur peut utiliser
# les commandes réservées à l'owner.
OWNER_ID = 142365250803466240

# Salons où l'auto-traduction est active (géré via la commande *auto)
auto_channels: set[int] = set()

# ─── Système de laisse (dog) ──────────────────────────────────
EMOJI_CHIEN = "🐕"
DATA_FILE = "laisse_data.json"  # créé automatiquement à côté de bot.py


def charger_donnees() -> dict:
    """Charge les laisses et la whitelist depuis le fichier JSON."""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("laisses", {})  # { "guild_id:user_id": {...} }
    d.setdefault("wl", [])       # [user_id, ...]
    return d


def sauver_donnees() -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


data = charger_donnees()


def cle_laisse(guild_id: int, user_id: int) -> str:
    return f"{guild_id}:{user_id}"


def formater_pseudo(base_name: str, maitre_name: str) -> str:
    """Construit '<pseudo> (🐕 de <maître>)' en respectant la limite Discord (32)."""
    suffixe = f" ({EMOJI_CHIEN} de {maitre_name})"
    place_dispo = 32 - len(suffixe)
    if place_dispo < 1:
        # Le nom du maître est trop long : on garde juste le suffixe tronqué
        return suffixe.strip()[:32]
    return base_name[:place_dispo] + suffixe

# ──────────────────────────────────────────────────────────────
#  TRADUCTION
# ──────────────────────────────────────────────────────────────

# Moteur DeepL optionnel : si la variable d'environnement DEEPL_API_KEY est
# définie, on l'utilise en priorité (meilleure qualité), avec repli sur Google.
DEEPL_KEY = os.environ.get("DEEPL_API_KEY")

# Langues connues : code -> (nom français, drapeau)
LANGUES = {
    "fr": ("Français", "🇫🇷"),
    "tr": ("Turc", "🇹🇷"),
    "en": ("Anglais", "🇬🇧"),
    "es": ("Espagnol", "🇪🇸"),
    "de": ("Allemand", "🇩🇪"),
    "it": ("Italien", "🇮🇹"),
    "pt": ("Portugais", "🇵🇹"),
    "nl": ("Néerlandais", "🇳🇱"),
    "ru": ("Russe", "🇷🇺"),
    "ar": ("Arabe", "🇸🇦"),
    "ja": ("Japonais", "🇯🇵"),
    "ko": ("Coréen", "🇰🇷"),
    "zh-CN": ("Chinois", "🇨🇳"),
    "pl": ("Polonais", "🇵🇱"),
    "ro": ("Roumain", "🇷🇴"),
    "uk": ("Ukrainien", "🇺🇦"),
    "el": ("Grec", "🇬🇷"),
    "sv": ("Suédois", "🇸🇪"),
    "hi": ("Hindi", "🇮🇳"),
}

# Réagir avec l'un de ces drapeaux traduit le message dans la langue associée.
DRAPEAU_VERS_LANGUE = {
    "🇫🇷": "fr", "🇹🇷": "tr", "🇬🇧": "en", "🇺🇸": "en", "🇪🇸": "es",
    "🇩🇪": "de", "🇮🇹": "it", "🇵🇹": "pt", "🇧🇷": "pt", "🇳🇱": "nl",
    "🇷🇺": "ru", "🇸🇦": "ar", "🇯🇵": "ja", "🇰🇷": "ko", "🇨🇳": "zh-CN",
    "🇵🇱": "pl", "🇷🇴": "ro", "🇺🇦": "uk", "🇬🇷": "el", "🇸🇪": "sv", "🇮🇳": "hi",
}

LIMITE_CARACTERES = 4500  # marge sous la limite des moteurs (~5000)


def info_langue(code: str | None) -> tuple[str, str]:
    """Renvoie (nom, drapeau) pour un code de langue, normalisé."""
    if not code:
        return ("Inconnue", "🌐")
    code = code.lower()
    if code in ("zh", "zh-cn"):
        code = "zh-CN"
    for k, v in LANGUES.items():
        if k.lower() == code.lower():
            return v
    return (code, "🌐")


def detecter_langue(texte: str) -> str | None:
    try:
        return detect(texte)
    except LangDetectException:
        return None


def _decouper(texte: str, taille: int = LIMITE_CARACTERES) -> list[str]:
    """Découpe un long texte en morceaux sous la limite, sans couper les mots."""
    if len(texte) <= taille:
        return [texte]
    morceaux, courant = [], ""
    for mot in texte.split(" "):
        if len(courant) + len(mot) + 1 > taille:
            if courant:
                morceaux.append(courant)
            courant = mot
        else:
            courant = f"{courant} {mot}".strip()
    if courant:
        morceaux.append(courant)
    return morceaux


def _moteur(texte: str, cible: str, source: str = "auto") -> str:
    """Traduit un morceau : DeepL si dispo (repli Google en cas d'échec)."""
    if DEEPL_KEY:
        try:
            from deep_translator import DeeplTranslator
            return DeeplTranslator(
                api_key=DEEPL_KEY, source=source, target=cible, use_free_api=True
            ).translate(texte)
        except Exception:
            pass  # repli silencieux sur Google
    return GoogleTranslator(source=source, target=cible).translate(texte)


# ══════════════════════════════════════════════════════════════
#  PRÉ-TRAITEMENT DU TURC  (pour une traduction TR→XX bien meilleure)
# ══════════════════════════════════════════════════════════════
# Le turc de chat est souvent : abrégé (slm, nbr, tmm), sans accents
# (cok au lieu de çok), avec des lettres répétées (çoook). Les moteurs de
# traduction s'en sortent mal. On "réécrit" donc le texte en turc correct
# AVANT de le traduire, ce qui améliore énormément la compréhension.

# 1) Argot / abréviations -> turc correct
ARGOT_TR = {
    "slm": "selam", "mrb": "merhaba", "mrhb": "merhaba",
    "nbr": "ne haber", "naber": "ne haber", "nrb": "ne haber", "napıyon": "ne yapıyorsun",
    "napıyosun": "ne yapıyorsun", "napıyorsun": "ne yapıyorsun", "naptın": "ne yaptın",
    "napcaz": "ne yapacağız", "noldu": "ne oldu", "noluyo": "ne oluyor",
    "sa": "selamün aleyküm", "as": "aleyküm selam",
    "tmm": "tamam", "tm": "tamam", "tamamdır": "tamam",
    "tşk": "teşekkürler", "tsk": "teşekkürler", "tskler": "teşekkürler", "tşkler": "teşekkürler",
    "eyw": "eyvallah", "eyv": "eyvallah",
    "knk": "kanka", "kanki": "kanka", "abicim": "abi",
    "bb": "bay bay", "gs": "görüşürüz", "grsrz": "görüşürüz", "grş": "görüşürüz",
    "cnm": "canım", "askm": "aşkım", "slmlr": "selamlar",
    "ewt": "evet", "evt": "evet", "hyr": "hayır", "yh": "ya", "yha": "ya",
    "bi": "bir", "bişi": "bir şey", "bişey": "bir şey", "bişeyler": "bir şeyler",
    "valla": "vallahi", "vallaha": "vallahi", "vallahi": "vallahi",
    "niye": "neden", "naptın": "ne yaptın", "knkam": "kankam",
    " msj": "mesaj", "msj": "mesaj", "selamun": "selamün",
}

# 2) Turc sans accents -> turc accentué (formes non ambiguës uniquement)
ACCENTS_TR = {
    "nasilsin": "nasılsın", "nasilsiniz": "nasılsınız", "nasil": "nasıl",
    "gunaydin": "günaydın", "iyiyim": "iyiyim", "tesekkur": "teşekkür",
    "tesekkurler": "teşekkürler", "tessekur": "teşekkür", "cok": "çok",
    "guzel": "güzel", "degil": "değil", "icin": "için", "simdi": "şimdi",
    "sey": "şey", "hersey": "her şey", "herseyi": "her şeyi", "herseye": "her şeye",
    "oyle": "öyle", "boyle": "böyle", "soyle": "söyle", "uzgunum": "üzgünüm",
    "gormek": "görmek", "goruyorum": "görüyorum", "gorusuruz": "görüşürüz",
    "dogru": "doğru", "yanlis": "yanlış", "gunes": "güneş", "kardes": "kardeş",
    "calisiyorum": "çalışıyorum", "calismak": "çalışmak", "calisma": "çalışma",
    "gidecegim": "gideceğim", "gelecegim": "geleceğim", "yapacagim": "yapacağım",
    "anladim": "anladım", "anlamadim": "anlamadım", "yapiyorum": "yapıyorum",
    "ozur": "özür", "lutfen": "lütfen", "hayir": "hayır", "dusunuyorum": "düşünüyorum",
    "gercekten": "gerçekten", "turkce": "türkçe", "fransizca": "fransızca",
    "universite": "üniversite", "ogrenci": "öğrenci", "ogretmen": "öğretmen",
    "sarki": "şarkı", "mumkun": "mümkün", "gunah": "günah", "mujde": "müjde",
    "cocuk": "çocuk", "gun": "gün", "bugun": "bugün", "dun": "dün",
    "yarin": "yarın", "sukur": "şükür", "soyluyorum": "söylüyorum",
    "biliyom": "biliyorum", "gidiyom": "gidiyorum", "geliyom": "geliyorum",
}

# Mots distinctement turcs : leur présence indique du turc (même sans accents)
INDICES_TURC = {
    "bir", "bu", "ben", "sen", "biz", "için", "icin", "çok", "cok", "değil", "degil",
    "var", "yok", "evet", "hayır", "hayir", "nasıl", "nasil", "nasılsın", "nasilsin",
    "selam", "merhaba", "teşekkürler", "tesekkurler", "kanka", "abi", "tamam",
    "naber", "valla", "günaydın", "gunaydin", "güzel", "guzel", "değilim",
    "ve", "ama", "şey", "sey", "çünkü", "cunku", "şimdi", "simdi", "kardeş", "kardes",
}

# Caractères propres au turc, minuscules ET majuscules. On exclut volontairement
# i / I (ambigus) et ç/ö/ü (présents en français).
_CHARS_TURCS = set("ışğİŞĞ")


def _reduire_repetitions(mot: str) -> str:
    """çoook -> çok, yaaa -> ya (réduit 3+ lettres identiques à 1)."""
    return re.sub(r"(.)\1{2,}", r"\1", mot)


def _formes_min(mot: str) -> tuple[str, ...]:
    """Renvoie les formes minuscules possibles d'un mot.

    En turc, 'I' majuscule devient 'ı' (sans point), alors qu'ailleurs c'est 'i'.
    On teste donc les DEUX versions pour que la détection marche en MAJUSCULES.
    Ex : 'NASILSIN' -> ('nasilsin', 'nasılsın')
    """
    standard = mot.lower()                                   # I -> i
    turc = mot.replace("İ", "i").replace("I", "ı").lower()   # I -> ı
    return (standard,) if standard == turc else (standard, turc)


def ressemble_turc(texte: str) -> bool:
    """Vrai si le texte est probablement du turc (même en majuscules / sans accents)."""
    if any(c in _CHARS_TURCS for c in texte):
        return True
    if detecter_langue(texte) == "tr":
        return True
    mots = re.findall(r"\w+", texte, re.UNICODE)
    hits = 0
    for m in mots:
        if any(
            f in INDICES_TURC or f in ARGOT_TR or f in ACCENTS_TR
            for f in _formes_min(m)
        ):
            hits += 1
    # 1 indice suffit pour un message court, 2 pour un message plus long
    return hits >= (1 if len(mots) <= 3 else 2)


def pretraiter_turc(texte: str) -> str:
    """Réécrit le turc de chat en turc correct avant traduction (majuscules incluses)."""
    morceaux = re.findall(r"\w+|[^\w\s]+|\s+", texte, re.UNICODE)
    sortie = []
    for tok in morceaux:
        if tok.isspace() or not re.match(r"\w", tok, re.UNICODE):
            sortie.append(tok)
            continue
        base = _reduire_repetitions(tok)
        remplacement = None
        for forme in _formes_min(base):
            if forme in ARGOT_TR:
                remplacement = ARGOT_TR[forme]
                break
            if forme in ACCENTS_TR:
                remplacement = ACCENTS_TR[forme]
                break
        sortie.append(remplacement if remplacement is not None else base)
    return "".join(sortie)


def traduire_texte(texte: str, cible: str = "fr") -> str:
    """Traduit un texte vers `cible`. Le turc bénéficie d'un pré-traitement
    dédié + d'un forçage de la langue source pour une qualité bien supérieure."""
    source = "auto"
    a_traduire = texte
    if cible != "tr" and ressemble_turc(texte):
        a_traduire = pretraiter_turc(texte)
        source = "tr"
    parties = [_moteur(m, cible, source) for m in _decouper(a_traduire)]
    return " ".join(p for p in parties if p)


# ──────────────────────────────────────────────────────────────
#  BOT
# ──────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # nécessaire pour le système de laisse (pseudos, arrivées)

# On retire la commande d'aide par défaut pour utiliser la nôtre
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} (id: {bot.user.id})")
    print(f"   Préfixe : {PREFIX}")


# ─── Check : réservé à l'owner ─────────────────────────────────
def est_owner():
    """Décorateur : ajoute @est_owner() au-dessus d'une commande
    pour la réserver à l'owner (OWNER_ID)."""
    async def predicate(ctx: commands.Context) -> bool:
        return ctx.author.id == OWNER_ID
    return commands.check(predicate)


# ─── Commande : traduire ──────────────────────────────────────
@bot.command(name="traduire", aliases=["tr"])
async def traduire(ctx: commands.Context, *, contenu: str = None):
    """Traduit un texte vers le français (ou une autre langue).

    Usages :
      • *traduire <texte>            → traduit vers le français
      • *traduire <code> <texte>     → traduit vers la langue <code> (ex : en, es, tr)
      • (en réponse à un message) *traduire [code]  → traduit le message visé
    """
    # Récupère le texte depuis le message auquel on répond, si besoin.
    texte = contenu
    cible = "fr"

    # Si le premier mot est un code de langue connu, c'est la cible.
    if contenu:
        premier, _, reste = contenu.partition(" ")
        codes_acceptes = {k.lower() for k in LANGUES} | {"zh"}
        if premier.lower() in codes_acceptes:
            cible = "zh-CN" if premier.lower() in ("zh", "zh-cn") else premier.lower()
            texte = reste.strip() or None

    # Cas réponse à un message : on traduit le message visé.
    if not texte and ctx.message.reference is not None:
        ref = ctx.message.reference
        message_cible = ref.resolved
        if message_cible is None or isinstance(
            message_cible, discord.DeletedReferencedMessage
        ):
            try:
                message_cible = await ctx.channel.fetch_message(ref.message_id)
            except Exception:
                message_cible = None
        if message_cible and message_cible.content:
            texte = message_cible.content

    if not texte:
        await ctx.reply(
            f"Utilisation : `{PREFIX}traduire <texte>` "
            f"ou `{PREFIX}traduire <code> <texte>` (ex : `en`, `es`, `tr`).\n"
            f"Tu peux aussi répondre à un message avec `{PREFIX}traduire`. "
            f"Liste des langues : `{PREFIX}langues`."
        )
        return

    try:
        async with ctx.typing():
            traduction = traduire_texte(texte, cible)
        code_source = detecter_langue(texte)
        nom_src, drap_src = info_langue(code_source)
        nom_dst, drap_dst = info_langue(cible)

        embed = discord.Embed(color=0x3B88C3)
        embed.add_field(name=f"{drap_src} {nom_src}", value=texte[:1024], inline=False)
        embed.add_field(
            name=f"{drap_dst} {nom_dst}", value=traduction[:1024], inline=False
        )
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Erreur lors de la traduction : {e}")


# ─── Commande : langues ───────────────────────────────────────
@bot.command(name="langues", aliases=["langs"])
async def langues(ctx: commands.Context):
    """Affiche les codes de langue disponibles."""
    lignes = [f"{drap} `{code}` — {nom}" for code, (nom, drap) in LANGUES.items()]
    embed = discord.Embed(
        title="🌐 Langues disponibles",
        description="\n".join(lignes),
        color=0x3B88C3,
    )
    embed.set_footer(
        text="Exemple : *traduire en bonjour  •  ou réagis avec un drapeau sur un message"
    )
    await ctx.reply(embed=embed, mention_author=False)


# ─── Commande : auto (réservée à l'owner) ─────────────────────
@bot.command(name="auto")
@est_owner()
async def auto(ctx: commands.Context):
    """Active ou désactive l'auto-traduction dans ce salon."""
    cid = ctx.channel.id
    if cid in auto_channels:
        auto_channels.discard(cid)
        await ctx.reply("🔴 Auto-traduction **désactivée** dans ce salon.")
    else:
        auto_channels.add(cid)
        await ctx.reply(
            "🟢 Auto-traduction **activée** dans ce salon.\n"
            "Les messages détectés comme turcs seront traduits automatiquement."
        )


# ──────────────────────────────────────────────────────────────
#  SYSTÈME DE LAISSE (DOG)
# ──────────────────────────────────────────────────────────────

def peut_laisser(user_id: int) -> bool:
    """L'owner et les whitelistés peuvent utiliser la laisse."""
    return user_id == OWNER_ID or user_id in data["wl"]


def nb_chiens(maitre_id: int) -> int:
    return sum(1 for v in data["laisses"].values() if v["master_id"] == maitre_id)


# ─── Commande : laisse / dog ──────────────────────────────────
@bot.command(name="laisse", aliases=["dog"])
async def laisse(ctx: commands.Context, membre: discord.Member = None):
    """Met une personne en laisse (ou la détache si elle l'est déjà)."""
    auteur = ctx.author.id

    if not peut_laisser(auteur):
        await ctx.reply("⛔ Tu n'as pas la permission d'utiliser cette commande.")
        return
    if membre is None:
        await ctx.reply(f"Utilisation : `{PREFIX}laisse <@membre ou id>`")
        return

    cle = cle_laisse(ctx.guild.id, membre.id)

    # ── Si déjà en laisse → on détache (toggle) ──
    if cle in data["laisses"]:
        info = data["laisses"][cle]
        if auteur != OWNER_ID and info["master_id"] != auteur:
            await ctx.reply("⛔ Ce chien appartient à quelqu'un d'autre.")
            return
        del data["laisses"][cle]
        sauver_donnees()
        try:
            await membre.edit(nick=info.get("base_name"))
        except discord.Forbidden:
            pass
        await ctx.reply(f"🦴 {membre.mention} n'est plus en laisse.")
        return

    # ── Sinon on attache ──
    # Limite : un WL ne peut avoir qu'un seul chien à la fois (l'owner = illimité)
    if auteur != OWNER_ID and nb_chiens(auteur) >= 1:
        await ctx.reply(
            "⛔ En tant que whitelisté, tu ne peux avoir qu'**un seul** chien à la fois."
        )
        return

    base_name = membre.display_name
    nouveau_nick = formater_pseudo(base_name, ctx.author.display_name)
    try:
        await membre.edit(nick=nouveau_nick)
    except discord.Forbidden:
        await ctx.reply(
            "❌ Impossible de renommer cette personne "
            "(son rôle est au-dessus du mien, ou c'est le propriétaire du serveur)."
        )
        return

    data["laisses"][cle] = {
        "master_id": auteur,
        "master_name": ctx.author.display_name,
        "base_name": base_name,
        "nick": nouveau_nick,
    }
    sauver_donnees()
    await ctx.reply(f"{EMOJI_CHIEN} {membre.mention} est maintenant en laisse.")


# ─── Commande : dogs (voir ses chiens) ────────────────────────
@bot.command(name="dogs", aliases=["chiens", "listdog"])
async def dogs(ctx: commands.Context):
    """Affiche la liste de tes chiens (toutes les laisses si tu es l'owner)."""
    auteur = ctx.author.id
    if not peut_laisser(auteur):
        await ctx.reply("⛔ Tu n'as pas la permission d'utiliser cette commande.")
        return

    if auteur == OWNER_ID:
        entrees = list(data["laisses"].items())
        titre = "🐕 Tous les chiens"
    else:
        entrees = [
            (k, v) for k, v in data["laisses"].items() if v["master_id"] == auteur
        ]
        titre = "🐕 Tes chiens"

    if not entrees:
        await ctx.reply("Aucun chien en laisse pour le moment.")
        return

    lignes = []
    for cle, info in entrees:
        _, user_id = cle.split(":")
        if auteur == OWNER_ID:
            lignes.append(f"• <@{user_id}> — maître : <@{info['master_id']}>")
        else:
            lignes.append(f"• <@{user_id}>")

    embed = discord.Embed(
        title=titre,
        description="\n".join(lignes),
        color=0x8B5A2B,
    )
    await ctx.reply(embed=embed, mention_author=False)


# ─── Commande : wl (gestion whitelist, owner only) ────────────
@bot.command(name="wl")
@est_owner()
async def wl(ctx: commands.Context, action: str = None, membre: discord.Member = None):
    """Gère la whitelist : *wl add|remove|list [@membre]"""
    if action == "add" and membre:
        if membre.id not in data["wl"]:
            data["wl"].append(membre.id)
            sauver_donnees()
        await ctx.reply(f"✅ {membre.mention} ajouté à la whitelist.")
    elif action in ("remove", "del", "retirer") and membre:
        if membre.id in data["wl"]:
            data["wl"].remove(membre.id)
            sauver_donnees()
        await ctx.reply(f"✅ {membre.mention} retiré de la whitelist.")
    elif action == "list":
        if not data["wl"]:
            await ctx.reply("La whitelist est vide.")
        else:
            lignes = "\n".join(f"• <@{uid}>" for uid in data["wl"])
            await ctx.reply(f"**Whitelist :**\n{lignes}")
    else:
        await ctx.reply(f"Utilisation : `{PREFIX}wl add|remove|list [@membre]`")


# ─── Événement : forcer le pseudo (anti-changement) ───────────
@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    cle = cle_laisse(after.guild.id, after.id)
    info = data["laisses"].get(cle)
    if not info:
        return
    if after.nick != info["nick"]:
        try:
            await after.edit(nick=info["nick"])
        except discord.Forbidden:
            pass


# ─── Événement : ré-appliquer la laisse au retour sur le serveur ─
@bot.event
async def on_member_join(membre: discord.Member):
    cle = cle_laisse(membre.guild.id, membre.id)
    info = data["laisses"].get(cle)
    if not info:
        return
    try:
        await membre.edit(nick=info["nick"])
    except discord.Forbidden:
        pass


# ─── Commande : aide ──────────────────────────────────────────
@bot.command(name="aide", aliases=["help"])
async def aide(ctx: commands.Context):
    """Affiche la liste des commandes."""
    embed = discord.Embed(
        title="🤖 Bot de traduction",
        description=f"Préfixe : `{PREFIX}`",
        color=0x3B88C3,
    )
    embed.add_field(
        name=f"{PREFIX}traduire <texte>  (ou {PREFIX}tr)",
        value=(
            "Détecte la langue et traduit vers le **français**.\n"
            f"• `{PREFIX}traduire <code> <texte>` → traduit vers une autre langue "
            "(ex : `en`, `es`, `tr`).\n"
            f"• Réponds à un message avec `{PREFIX}traduire [code]` pour le traduire."
        ),
        inline=False,
    )
    embed.add_field(
        name="Réactions drapeaux 🇬🇧 🇹🇷 🇪🇸 …",
        value="Réagis sur un message avec un drapeau pour le traduire dans cette langue.",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}langues",
        value="Affiche tous les codes de langue disponibles.",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}auto",
        value="Active/désactive l'auto-traduction du salon (toute langue → français).",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}aide",
        value="Affiche ce message d'aide.",
        inline=False,
    )

    # Commandes de laisse : visibles seulement par l'owner et les WL
    if peut_laisser(ctx.author.id):
        embed.add_field(
            name="\u200b",
            value="**🐕 Système de laisse**",
            inline=False,
        )
        embed.add_field(
            name=f"{PREFIX}laisse <@membre>  (ou {PREFIX}dog)",
            value="Met la personne en laisse, ou la détache si elle l'est déjà.",
            inline=False,
        )
        embed.add_field(
            name=f"{PREFIX}dogs",
            value="Affiche la liste de tes chiens.",
            inline=False,
        )
        if ctx.author.id == OWNER_ID:
            embed.add_field(
                name=f"{PREFIX}wl add|remove|list [@membre]",
                value="Gère la whitelist (owner uniquement).",
                inline=False,
            )

    await ctx.reply(embed=embed, mention_author=False)


# ─── Auto-traduction des messages ─────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Auto-traduction si le salon est activé (et que ce n'est pas une commande)
    if (
        message.channel.id in auto_channels
        and message.content
        and not message.content.startswith(PREFIX)
        and len(message.content) >= 4  # évite les faux positifs sur messages courts
    ):
        langue = detecter_langue(message.content)
        if langue and langue.lower() != "fr":
            try:
                traduction = traduire_texte(message.content, "fr")
                nom, drapeau = info_langue(langue)
                await message.reply(
                    f"🇫🇷 {traduction}  *( {drapeau} {nom} )*", mention_author=False
                )
            except Exception as e:
                print(f"Erreur de traduction : {e}")

    # Indispensable pour que les commandes continuent de fonctionner
    await bot.process_commands(message)


# ─── Traduction par réaction (n'importe quel drapeau) ─────────
@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    cible = DRAPEAU_VERS_LANGUE.get(str(reaction.emoji))
    if not cible:
        return
    message = reaction.message
    if not message.content:
        return
    try:
        traduction = traduire_texte(message.content, cible)
        _, drapeau = info_langue(cible)
        await message.reply(f"{drapeau} {traduction}", mention_author=False)
    except Exception as e:
        print(f"Erreur de traduction : {e}")


# ─── Gestion des erreurs de commande ──────────────────────────
@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        await ctx.reply("⛔ Cette commande est réservée à l'owner du bot.")
    elif isinstance(error, commands.CommandNotFound):
        pass  # on ignore les commandes inconnues
    else:
        raise error


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "❌ Aucun token trouvé. Définis la variable d'environnement "
            "DISCORD_TOKEN avant de lancer le bot."
        )
    bot.run(TOKEN)
