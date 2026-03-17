from aiogram import types, html

from chainnokizuna.core.resources import vp_bot


class Player:
    __slots__ = (
        "_username", "_name", "user_id", "is_vp", "word_count", "letter_count", "longest_word", "score"
    )

    def __init__(self, user: types.User) -> None:
        self._username = user.username
        self._name = user.full_name
        self.user_id = user.id

        from chainnokizuna.core.resources import GlobalState
        self.is_vp = (
            vp_bot is not None 
            and GlobalState.vp_user is not None 
            and user.id == GlobalState.vp_user.id
        )
        self.word_count = 0
        self.letter_count = 0
        self.longest_word = ""

        # For elimination games only
        # Though generally score = letter count,
        # there is turn score increment ceiling for more balanced gameplay
        self.score = 0

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "username": self._username,
            "full_name": self._name,
            "is_vp": self.is_vp,
            "word_count": self.word_count,
            "letter_count": self.letter_count,
            "longest_word": self.longest_word,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        """Reconstruct a Player from serialized data without needing a types.User."""
        player = object.__new__(cls)
        player._username = data.get("username")
        player._name = data.get("full_name", "Unknown")
        player.user_id = data["user_id"]
        player.is_vp = data.get("is_vp", False)
        player.word_count = data.get("word_count", 0)
        player.letter_count = data.get("letter_count", 0)
        player.longest_word = data.get("longest_word", "")
        player.score = data.get("score", 0)
        return player

    @property
    def name(self) -> str:
        if self._username:
            return f"<a href='https://t.me/{self._username}'>{html.quote(self._name)}</a>"
        else:
            return f"<b>{html.quote(self._name)}</b>"

    @property
    def mention(self) -> str:
        return f"<a href='tg://user?id={self.user_id}'>{html.quote(self._name)}</a>"

    @classmethod
    async def create(cls, user: types.User) -> "Player":
        return Player(user)

    @staticmethod
    async def vp() -> "Player":
        if vp_bot is None:
            raise RuntimeError("Virtual player bot is not enabled.")
        vp_user = await vp_bot.me()
        return Player(vp_user)
