from framework.plugin import BasePlugin
from framework.event import Event, PlainEvent
from framework.agent import PluginFieldEvent
from typing import TypedDict
from dataclasses import dataclass
import pathlib


class PetState(TypedDict):
    mood: int
    health: int
    hunger: int


PET_STATE_RANGE = {
    "mood": (-100, 100),
    "health": (0, 100),
    "hunger": (0, 100),
}
PET_STATE_DESC_MAPPER = {
    "mood": {
        90: "very happy",
        60: "happy",
        40: "good",
        10: "fair",
        -20: "bad",
        -40: "very bad",
        -70: "terrible",
        -999: "despair",
    },
    "health": {
        90: "healthy",
        80: "slightly uncomfortable",
        70: "uncomfortable",
        60: "ill",
        40: "critically ill",
        -999: "dying",
    },
    "hunger": {
        90: "stuffed",
        80: "full",
        70: "satisfied",
        60: "slightly hungry",
        50: "hungry",
        30: "very hungry",
        -999: "starving",
    },
}


@dataclass
class ModifyPetStateEvent(Event):
    state_name: str
    delta: int


class Plugin(BasePlugin):

    def init(self): 
        self.state = PetState(mood=50, health=98, hunger=90)

    def prompts(self):
        return {"json_fields": pathlib.Path(__file__).with_name("mood_field.md")}

    def infos(self):
        return {
            "Current State": {
                name: self.state_desc(name) for name in PET_STATE_DESC_MAPPER.keys()
            }
        }

    def on_event(self, e):
        match e:
            case PluginFieldEvent("mood_delta", delta):
                self.trigger_event(ModifyPetStateEvent("mood", delta))
            case ModifyPetStateEvent(name, delta):
                self.modify_state(name, delta)

    def state_desc(self, name: str):
        value = self.state[name]
        for lb, desc in PET_STATE_DESC_MAPPER[name].items():
            if value > lb:
                return desc

    @staticmethod
    def state_modify_check(name: str, value: int):
        return min(
            max(
                value,
                PET_STATE_RANGE[name][0],
            ),
            PET_STATE_RANGE[name][1],
        )

    def modify_state(self, name: str, delta: int):
        prev_desc = self.state_desc(name)
        self.state[name] = self.state_modify_check(
            name, self.state[name] + delta
        )
        new_desc = self.state_desc(name)

        if delta > 0:
            base_text = f'Your "{name}" state value increased.'
        elif delta < 0:
            base_text = f'Your "{name}" state value decreased.'
        else:
            raise

        if new_desc != prev_desc:
            extra_text = f'Your "{name}" state changes from "{prev_desc}" to "{new_desc}"'
        else:
            extra_text = ""

        # return base_text + extra_text
        if len(extra_text):
            self.trigger_event(PlainEvent(extra_text))



