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

def traduire_tr_vers_fr(texte: str) -> str:
    return GoogleTranslator(source="tr", target="fr").translate(texte)


def est_probablement_turc(texte: str) -> bool:
    try:
        return detect(texte) == "tr"
    except LangDetectException:
        return False


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
async def traduire(ctx: commands.Context, *, texte: str = None):
    """Traduit un texte turc en français.

    Deux usages :
      • *traduire <texte>        → traduit le texte fourni
      • (en réponse à un message) *traduire  → traduit le message auquel tu réponds
    """
    # Si aucun texte n'est donné mais que la commande répond à un message,
    # on traduit le contenu du message auquel l'utilisateur répond.
    if not texte and ctx.message.reference is not None:
        ref = ctx.message.reference
        message_cible = ref.resolved
        # Si Discord n'a pas déjà résolu le message, on va le chercher
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
            f"Utilisation : `{PREFIX}traduire <texte en turc>`\n"
            f"Ou réponds à un message turc avec `{PREFIX}traduire`."
        )
        return
    try:
        traduction = traduire_tr_vers_fr(texte)
        embed = discord.Embed(color=0xE30A17)  # rouge turc
        embed.add_field(name="🇹🇷 Turc", value=texte, inline=False)
        embed.add_field(name="🇫🇷 Français", value=traduction, inline=False)
        await ctx.reply(embed=embed, mention_author=False)
    except Exception as e:
        await ctx.reply(f"❌ Erreur lors de la traduction : {e}")


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
        title="🤖 Bot de traduction Turc → Français",
        description=f"Préfixe : `{PREFIX}`",
        color=0xE30A17,
    )
    embed.add_field(
        name=f"{PREFIX}traduire <texte>  (ou {PREFIX}tr)",
        value=(
            "Traduit le texte turc en français.\n"
            f"Astuce : réponds à un message turc avec `{PREFIX}traduire` "
            "(sans rien écrire) pour traduire ce message."
        ),
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}auto",
        value="Active/désactive l'auto-traduction dans le salon actuel.",
        inline=False,
    )
    embed.add_field(
        name=f"{PREFIX}aide",
        value="Affiche ce message d'aide.",
        inline=False,
    )
    embed.add_field(
        name="Réaction 🇹🇷",
        value="Réagis avec 🇹🇷 sur un message pour le traduire.",
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
    ):
        if est_probablement_turc(message.content):
            try:
                traduction = traduire_tr_vers_fr(message.content)
                await message.reply(f"🇫🇷 {traduction}", mention_author=False)
            except Exception as e:
                print(f"Erreur de traduction : {e}")

    # Indispensable pour que les commandes continuent de fonctionner
    await bot.process_commands(message)


# ─── Traduction par réaction 🇹🇷 ──────────────────────────────
@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot or str(reaction.emoji) != TRIGGER_EMOJI:
        return
    message = reaction.message
    if not message.content:
        return
    try:
        traduction = traduire_tr_vers_fr(message.content)
        await message.reply(f"🇫🇷 {traduction}", mention_author=False)
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
