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
import discord
from discord.ext import commands
from deep_translator import GoogleTranslator
from langdetect import detect, LangDetectException

# ──────────────────────────────────────────────────────────────
#  CONFIGURATION
# ──────────────────────────────────────────────────────────────

TOKEN = os.environ.get("DISCORD_TOKEN")
PREFIX = "*"
TRIGGER_EMOJI = "🇹🇷"

# Salons où l'auto-traduction est active (géré via la commande *auto)
auto_channels: set[int] = set()

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

# On retire la commande d'aide par défaut pour utiliser la nôtre
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f"✅ Connecté en tant que {bot.user} (id: {bot.user.id})")
    print(f"   Préfixe : {PREFIX}")


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


# ─── Commande : auto ──────────────────────────────────────────
@bot.command(name="auto")
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


# ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit(
            "❌ Aucun token trouvé. Définis la variable d'environnement "
            "DISCORD_TOKEN avant de lancer le bot."
        )
    bot.run(TOKEN)