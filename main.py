# language: Python 3.11, file: main.py, target: Railway, discord.py-self
# pip install discord.py-self PyNaCl
# WARNING: user-token self-bot = ToS violation, ban risk. Runs as you.
# Reconnects on network drop / gateway reset / redeploy.
# Does NOT rejoin after a permission denial, does NOT fight a duplicate session.

import asyncio
import logging
import os

import discord
from discord.opus import Encoder
from discord.ext import tasks

TOKEN = os.environ["MTIwMDkwNjg0ODgyNDQ2MzQwMQ.GeDFSK.yoYGgXTqwtxMjsa8ejtqBvTErvD_tANlAxeb8A"]
GUILD_ID = 1506048693693059212
CHANNEL_ID = 1516605858660880484

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vc")

FRAME_SIZE = 960 * 2 * 2
SILENCE_PCM = b"\x00" * FRAME_SIZE
MAX_BACKOFF = 60


class SilenceSource(discord.AudioSource):
    def __init__(self):
        self.encoder = Encoder()
        self.encoder.set_bitrate(64000)
        self._frame = self.encoder.encode(SILENCE_PCM, FRAME_SIZE)

    def read(self) -> bytes:
        return self._frame

    def is_opus(self) -> bool:
        return True

    def cleanup(self):
        pass


class VoiceHold(discord.Client):
    def __init__(self):
        super().__init__()
        self.vc: discord.VoiceClient | None = None
        self.perm_denied = False
        self._reconnect_lock = asyncio.Lock()

    async def on_ready(self):
        log.info("logged in as %s (id=%s)", self.user, self.user.id)
        await self.ensure_connected()
        if not self.hold.is_running():
            self.hold.start()

    async def ensure_connected(self):
        async with self._reconnect_lock:
            if self.perm_denied:
                return
            guild = self.get_guild(GUILD_ID)
            if guild is None:
                log.error("guild %s not found", GUILD_ID)
                return
            channel = guild.get_channel(CHANNEL_ID)
            if not isinstance(channel, discord.VoiceChannel):
                log.error("voice channel %s not found", CHANNEL_ID)
                return

            perms = channel.permissions_for(guild.me)
            log.info(
                "perms on %s: connect=%s speak=%s view=%s",
                channel.name, perms.connect, perms.speak, perms.view_channel,
            )
            if not perms.view_channel or not perms.connect:
                log.error("missing view_channel/connect — locked channel blocks a fresh join")
                self.perm_denied = True
                return

            if self.vc and self.vc.is_connected():
                if not self.vc.is_playing():
                    self.vc.play(SilenceSource())
                return

            backoff = 2
            while not self.perm_denied:
                try:
                    if guild.voice_client and guild.voice_client.is_connected():
                        self.vc = guild.voice_client
                        log.info("bound to existing voice client on %s", self.vc.channel)
                    else:
                        self.vc = await channel.connect(
                            self_deaf=False, self_mute=False, reconnect=True, timeout=20.0
                        )
                        log.info("joined %s", channel)
                    if not self.vc.is_playing():
                        self.vc.play(SilenceSource())
                    return
                except discord.Forbidden as e:
                    log.error("forbidden on join (code=%s): %s", getattr(e, "code", "?"), e)
                    self.perm_denied = True
                    return
                except (discord.HTTPException, asyncio.TimeoutError, OSError) as e:
                    log.warning("join failed (%s), retry in %ss", type(e).__name__, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)
                except Exception as e:
                    log.warning("join failed (%s), retry in %ss", e, backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF)

    @tasks.loop(seconds=20)
    async def hold(self):
        if self.perm_denied:
            return
        if not self.vc or not self.vc.is_connected():
            log.info("watchdog: not connected, reconnecting")
            await self.ensure_connected()
            return
        if self.vc.channel and self.vc.channel.id != CHANNEL_ID:
            log.info("watchdog: moved to %s, rejoining target", self.vc.channel)
            try:
                await self.vc.disconnect(force=True)
            except Exception:
                pass
            self.vc = None
            await self.ensure_connected()
            return
        if not self.vc.is_playing():
            log.info("watchdog: audio stopped, restarting silence")
            try:
                self.vc.play(SilenceSource())
            except Exception as e:
                log.warning("replay failed: %s", e)

    async def on_voice_state_update(self, member, before, after):
        if member.id != self.user.id or self.perm_denied:
            return
        if after.channel is None and before.channel is not None:
            log.info("dropped from voice, reconnecting in 3s")
            await asyncio.sleep(3)
            self.vc = None
            await self.ensure_connected()
        elif after.channel and after.channel.id != CHANNEL_ID:
            log.info("moved to %s, rejoining target in 3s", after.channel)
            await asyncio.sleep(3)
            try:
                await after.channel.guild.voice_client.disconnect(force=True)
            except Exception:
                pass
            self.vc = None
            await self.ensure_connected()


async def main():
    client = VoiceHold()
    async with client:
        await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
