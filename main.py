import asyncio
import logging
import logging.config
import re
from dataclasses import dataclass
from pathlib import Path

from lxml import html
from minio import Minio
from minio_helpers import minio_fput_object, minio_put_object_in_json
from playwright.async_api import BrowserContext
from playwright_helpers.browser_context import get_browser_context
from playwright_helpers.page import get_page, page_goto
from pydantic import SecretStr
from python_helpers.env import parse_env
from python_helpers.logging import init_logging
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
)


@dataclass(slots=True, frozen=True)
class ENV:
    MINIO_URL: str
    MINIO_ACCESS_KEY: SecretStr
    MINIO_SECRET_KEY: SecretStr
    MINIO_BUCKET_NAME: str

    LOL_WIKI_BASE_URL: str = "https://leagueoflegends.fandom.com"
    CHAMPION_DETAILS_MAX_NUM_CONCURRENT_PARSERS: int = 3
    MAX_NUM_CHAMPIONS: int | None = None

    DEBUG: bool = False
    RICH: bool = False
    OUTPUT_DIR: Path = Path("/app/out")
    OUTPUT_FILE_NAME: str = "champions.json"
    TRACES_FILE_NAME: str = "traces.zip"


@dataclass(slots=True, frozen=True)
class ChampionEntry:
    name: str
    last_changed_patch: str
    stats_url: str


async def parse_champion_entrys(
    browser_context: BrowserContext,
    lol_wiki_base_url: str,
    logger: logging.Logger,
) -> list[ChampionEntry]:
    async with get_page(browser_context=browser_context) as page:
        url = f"{lol_wiki_base_url}/wiki/List_of_champions"
        context = f"{page_goto.__name__} {url=}"
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            before=lambda _: logging.info(f"{context} {_.attempt_number=} started"),
            before_sleep=lambda _: logging.info(f"{context} {_.attempt_number=} failed: {_.outcome and _.outcome.exception()}, pending retry..."),
            reraise=True,
        ):
            with attempt:
                await page_goto(
                    page=page,
                    url=url,
                    wait_until="domcontentloaded",
                    timeout_sec=10,
                    num_scrolls=0,
                )

        table_root = html.fromstring(
            # (ref.) [How to get outer html from python playwright locator object?](https://stackoverflow.com/questions/70891225/how-to-get-outer-html-from-python-playwright-locator-object)
            html=await page.locator('//div[@id="content"]//table[contains(@class,"article-table")]').first.evaluate("el => el.outerHTML"),
            base_url=lol_wiki_base_url,
        )

    champion_entrys = list[ChampionEntry]()
    for row in table_root.xpath("//tbody/tr"):
        name = row.xpath("./td[1]")[0].get("data-sort-value")
        last_changed_patch = row.xpath("./td[4]")[0].text_content().strip()
        stats_url = table_root.base_url + row.xpath("./td[1]//a")[0].get("href")
        logger.debug(f"{name=} {last_changed_patch=} {stats_url=}")

        champion_entrys.append(
            ChampionEntry(
                name=name,
                last_changed_patch=last_changed_patch,
                stats_url=stats_url,
            )
        )

    return champion_entrys


@dataclass(slots=True)
class ChampionStats:
    name: str
    health_base: str | None = None
    health_growth: str | None = None
    resource_name: str | None = None
    resource_base: str | None = None
    resource_growth: str | None = None
    health_regen_base: str | None = None
    health_regen_growth: str | None = None
    resource_regen_base: str | None = None
    resource_regen_growth: str | None = None
    armor_base: str | None = None
    armor_growth: str | None = None
    attack_base: str | None = None
    attack_growth: str | None = None
    magic_resist_base: str | None = None
    magic_resist_growth: str | None = None
    crit_damage_percentage: str | None = None
    movement_speed: str | None = None
    attack_range: str | None = None
    attack_speed_base: str | None = None
    attack_windup_percentage: str | None = None
    attack_speed_ratio: str | None = None
    attack_speed_bonus_percentage: str | None = None
    missile_speed: str | None = None
    gameplay_radius: str | None = None
    selection_radius: str | None = None
    pathing_radius: str | None = None
    acquisition_radius: str | None = None
    aram_damage_dealt_bonus_percentage: str | None = None
    aram_damage_taken_bonus_percentage: str | None = None
    aram_attack_speed_bonus_percentage: str | None = None
    aram_ability_haste_bonus: str | None = None
    aram_energy_regen_bonus_percentage: str | None = None
    aram_healing_bonus_percentage: str | None = None
    aram_shielding_bonus_percentage: str | None = None
    aram_tenacity_bonus_percentage: str | None = None


HEALTH_REGEX = re.compile(r"Health (?P<health_base>[\d.]+)( \(\+ (?P<health_growth>[\d.]+)\))?", re.IGNORECASE)
RESOURCE_REGEX = re.compile(r"(?P<resource_name>[ a-zA-Z]+) (?P<resource_base>N/A|[\d.]+)( \(\+ (?P<resource_growth>[\d.]+)\))?", re.IGNORECASE)
HEALTH_REGEN_REGEX = re.compile(r"Health regen. \(per 5s\) (?P<health_regen_base>[\d.]+)( \(\+ (?P<health_regen_growth>[\d.]+)\))?", re.IGNORECASE)
RESOURCE_REGEN_REGEX = re.compile(r"[ a-zA-Z]* regen.( \(per 5s\))? (?P<resource_regen_base>N/A|[\d.]+)( \(\+ (?P<resource_regen_growth>[\d.]+)\))?", re.IGNORECASE)
ARMOR_REGEX = re.compile(r"Armor (?P<armor_base>[\d.]+)( \(\+ (?P<armor_growth>[\d.]+)\))?", re.IGNORECASE)
ATTACK_REGEX = re.compile(r"Attack damage (?P<attack_base>[\d.]+)( \(\+ (?P<attack_growth>[\d.]+)\))?", re.IGNORECASE)
MAGIC_RESIST_REGEX = re.compile(r"Magic resist. (?P<magic_resist_base>[\d.]+)( \(\+ (?P<magic_resist_growth>[\d.]+)\))?", re.IGNORECASE)
CRIT_DAMAGE_PERCENTAGE_REGEX = re.compile(r"Crit. damage (?P<crit_damage_percentage>[\d.]+)%", re.IGNORECASE)
MOVEMENT_SPEED_REGEX = re.compile(r"Move. speed (?P<movement_speed>[\d.]+)", re.IGNORECASE)
ATTACK_RANGE_REGEX = re.compile(r"Attack range (?P<attack_range>[\d.]+)", re.IGNORECASE)
ATTACK_SPEED_BASE_REGEX = re.compile(r"Base AS (?P<attack_speed_base>[\d.]+)", re.IGNORECASE)
ATTACK_WINDUP_PERCENTAGE_REGEX = re.compile(r"Attack windup (?P<attack_windup_percentage>[\d.]+)%", re.IGNORECASE)
ATTACK_SPEED_RATIO_REGEX = re.compile(r"AS ratio (?P<attack_speed_ratio>[\d.]+)", re.IGNORECASE)
ATTACK_SPEED_BONUS_PERCENTAGE_REGEX = re.compile(r"Bonus AS (?P<attack_speed_bonus_percentage>[\d.]+) %", re.IGNORECASE)
MISSILE_SPEED_REGEX = re.compile(r"Missile speed (?P<missile_speed>[\d.]+)", re.IGNORECASE)
GAMEPLAY_RADIUS_REGEX = re.compile(r"Gameplay radius (?P<gameplay_radius>[\d.]+)", re.IGNORECASE)
SELECTION_RADIUS_REGEX = re.compile(r"Selection radius (?P<selection_radius>[\d.]+)", re.IGNORECASE)
PATHING_RADIUS_REGEX = re.compile(r"Pathing radius (?P<pathing_radius>[\d.]+)", re.IGNORECASE)
ACQUISITION_RADIUS_REGEX = re.compile(r"Acq. radius (?P<acquisition_radius>[\d.]+)", re.IGNORECASE)
ARAM_DAMEGE_DEALT_BONUS_PERCENTAGE_REGEX = re.compile(r"Damage Dealt (?P<aram_damage_dealt_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_DAMEGE_TAKEN_BONUS_PERCENTAGE_REGEX = re.compile(r"Damage Received (?P<aram_damage_taken_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_ATTACK_SPEED_BONUS_PERCENTAGE_REGEX = re.compile(r"Total Attack Speed (?P<aram_attack_speed_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_ABILITY_HASTE_BONUS_REGEX = re.compile(r"Ability Haste (?P<aram_ability_haste_bonus>[\+\-][\d.]+)", re.IGNORECASE)
ARAM_ENERGY_REGEN_BONUS_PERCENTAGE_REGEX = re.compile(r"Energy Regen (?P<aram_energy_regen_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_HEALING_BONUS_PERCENTAGE_REGEX = re.compile(r"Healing (?P<aram_healing_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_SHIELDING_BONUS_PERCENTAGE_REGEX = re.compile(r"Shielding (?P<aram_shielding_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)
ARAM_TENACITY_BONUS_PERCENTAGE_REGEX = re.compile(r"Tenacity & Slow Resist (?P<aram_tenacity_bonus_percentage>[\+\-][\d.]+)%", re.IGNORECASE)


async def parse_champion_stats(
    champion_listing_result: ChampionEntry,
    browser_context: BrowserContext,
    sem: asyncio.Semaphore,
    logger: logging.Logger,
) -> ChampionStats:
    async with sem:
        async with get_page(browser_context=browser_context) as page:
            url = champion_listing_result.stats_url
            context = f"{page_goto.__name__} {url=}"
            details_locator = page.locator('//div[@id="content"]//div[contains(@class,"parser-output")]//div[contains(@class,"lvlselect") and ./aside]').first
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                before=lambda _: logging.info(f"{context} {_.attempt_number=} started"),
                before_sleep=lambda _: logging.info(f"{context} {_.attempt_number=} failed: {_.outcome and _.outcome.exception()}, pending retry..."),
                reraise=True,
            ):
                with attempt:
                    await page_goto(
                        page=page,
                        url=champion_listing_result.stats_url,
                        wait_until="domcontentloaded",
                        timeout_sec=10,
                        num_scrolls=0,
                    )
                    await details_locator.locator('//select[starts-with(@id,"lvl_")]').first.select_option(value="-1")

            # (ref.) [How to get outer html from python playwright locator object?](https://stackoverflow.com/questions/70891225/how-to-get-outer-html-from-python-playwright-locator-object)
            details_root = html.fromstring(await details_locator.evaluate("el => el.outerHTML"))

    champion_stats = ChampionStats(name=champion_listing_result.name)
    for el in details_root.xpath("//div[@data-source]"):
        field_name = str(el.get("data-source"))
        text = " ".join(s.strip() for s in el.itertext() if s.strip() != "")
        logger.debug(f"{champion_stats.name=} {field_name=} {text=}")

        if not text:
            continue
        match field_name:
            case "health":
                if (m := HEALTH_REGEX.match(text)) is not None:
                    champion_stats.health_base = m.group("health_base")
                    champion_stats.health_growth = m.group("health_growth")
            case "resource":
                if (m := RESOURCE_REGEX.match(text)) is not None:
                    if (resource_name := m.group("resource_name")) != "Resource":
                        champion_stats.resource_name = resource_name
                        champion_stats.resource_base = m.group("resource_base")
                        champion_stats.resource_growth = m.group("resource_growth")
            case "health regen":
                if (m := HEALTH_REGEN_REGEX.match(text)) is not None:
                    champion_stats.health_regen_base = m.group("health_regen_base")
                    champion_stats.health_regen_growth = m.group("health_regen_growth")
            case "resource regen":
                if (m := RESOURCE_REGEN_REGEX.match(text)) is not None:
                    if (resource_regen_base := m.group("resource_regen_base")) != "N/A":
                        champion_stats.resource_regen_base = resource_regen_base
                        champion_stats.resource_regen_growth = m.group("resource_regen_growth")
            case "armor":
                if (m := ARMOR_REGEX.match(text)) is not None:
                    champion_stats.armor_base = m.group("armor_base")
                    champion_stats.armor_growth = m.group("armor_growth")
            case "attack damage":
                if (m := ATTACK_REGEX.match(text)) is not None:
                    champion_stats.attack_base = m.group("attack_base")
                    champion_stats.attack_growth = m.group("attack_growth")
            case "mr":
                if (m := MAGIC_RESIST_REGEX.match(text)) is not None:
                    champion_stats.magic_resist_base = m.group("magic_resist_base")
                    champion_stats.magic_resist_growth = m.group("magic_resist_growth")
            case "critical damage":
                if (m := CRIT_DAMAGE_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.crit_damage_percentage = m.group("crit_damage_percentage")
            case "ms":
                if (m := MOVEMENT_SPEED_REGEX.match(text)) is not None:
                    champion_stats.movement_speed = m.group("movement_speed")
            case "range":
                if (m := ATTACK_RANGE_REGEX.match(text)) is not None:
                    champion_stats.attack_range = m.group("attack_range")
            case "attack speed":
                if (m := ATTACK_SPEED_BASE_REGEX.match(text)) is not None:
                    champion_stats.attack_speed_base = m.group("attack_speed_base")
            case "windup":
                if (m := ATTACK_WINDUP_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.attack_windup_percentage = m.group("attack_windup_percentage")
            case "as ratio":
                if (m := ATTACK_SPEED_RATIO_REGEX.match(text)) is not None:
                    champion_stats.attack_speed_ratio = m.group("attack_speed_ratio")
            case "bonus as":
                if (m := ATTACK_SPEED_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.attack_speed_bonus_percentage = m.group("attack_speed_bonus_percentage")
            case "missile speed":
                if (m := MISSILE_SPEED_REGEX.match(text)) is not None:
                    champion_stats.missile_speed = m.group("missile_speed")
            case "gameplay radius":
                if (m := GAMEPLAY_RADIUS_REGEX.match(text)) is not None:
                    champion_stats.gameplay_radius = m.group("gameplay_radius")
            case "selection radius":
                if (m := SELECTION_RADIUS_REGEX.match(text)) is not None:
                    champion_stats.selection_radius = m.group("selection_radius")
            case _ if "pathing radius" in field_name:
                if (m := PATHING_RADIUS_REGEX.match(text)) is not None:
                    champion_stats.pathing_radius = m.group("pathing_radius")
            case "acquisition radius":
                if (m := ACQUISITION_RADIUS_REGEX.match(text)) is not None:
                    champion_stats.acquisition_radius = m.group("acquisition_radius")
            case "aram-dmg-dealt":
                if (m := ARAM_DAMEGE_DEALT_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_damage_dealt_bonus_percentage = m.group("aram_damage_dealt_bonus_percentage")
            case "aram-dmg-taken":
                if (m := ARAM_DAMEGE_TAKEN_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_damage_taken_bonus_percentage = m.group("aram_damage_taken_bonus_percentage")
            case "aram_attack_speed":
                if (m := ARAM_ATTACK_SPEED_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_attack_speed_bonus_percentage = m.group("aram_attack_speed_bonus_percentage")
            case "aram_ability_haste":
                if (m := ARAM_ABILITY_HASTE_BONUS_REGEX.match(text)) is not None:
                    champion_stats.aram_ability_haste_bonus = m.group("aram_ability_haste_bonus")
            case "aram_energy_regen":
                if (m := ARAM_ENERGY_REGEN_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_energy_regen_bonus_percentage = m.group("aram_energy_regen_bonus_percentage")
            case "aram-healing":
                if (m := ARAM_HEALING_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_healing_bonus_percentage = m.group("aram_healing_bonus_percentage")
            case "aram-shielding":
                if (m := ARAM_SHIELDING_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_shielding_bonus_percentage = m.group("aram_shielding_bonus_percentage")
            case "aram_tenacity":
                if (m := ARAM_TENACITY_BONUS_PERCENTAGE_REGEX.match(text)) is not None:
                    champion_stats.aram_tenacity_bonus_percentage = m.group("aram_tenacity_bonus_percentage")
            case _ if any(
                field_name.startswith(prefix)
                for prefix in [
                    "nb-",
                    "nb_",
                    "ofa-",
                    "ofa_",
                    "urf-",
                    "usb-",
                    "usb_",
                    "ar_",
                ]
            ):
                pass
            case _:
                logger.warning(f"got unknown field {champion_stats.name=} {field_name=} {text=}")

    return champion_stats


async def main():
    env = parse_env(ENV)
    init_logging(show_debug=env.DEBUG, enable_rich=env.RICH)
    logger = logging.getLogger(__name__)
    logger.info(f"{env=}")

    # configure minio_client
    minio_client = Minio(
        endpoint=env.MINIO_URL,
        access_key=env.MINIO_ACCESS_KEY.get_secret_value(),
        secret_key=env.MINIO_SECRET_KEY.get_secret_value(),
        secure=False,
    )

    # crawl data
    sem = asyncio.Semaphore(env.CHAMPION_DETAILS_MAX_NUM_CONCURRENT_PARSERS)
    async with get_browser_context(traces_output_path=(env.OUTPUT_DIR / env.TRACES_FILE_NAME)) as browser_context:
        champion_listing_results = await parse_champion_entrys(
            browser_context=browser_context,
            lol_wiki_base_url=env.LOL_WIKI_BASE_URL,
            logger=logger,
        )
        champion_detailss = await asyncio.gather(
            *[
                parse_champion_stats(
                    champion_listing_result=r,
                    browser_context=browser_context,
                    sem=sem,
                    logger=logger,
                )
                for r in champion_listing_results[: env.MAX_NUM_CHAMPIONS]
            ]
        )

    minio_put_object_in_json(
        obj=[
            {
                "listing_result": champion_listing_result,
                "details": champion_details,
            }
            for champion_listing_result, champion_details in zip(champion_listing_results, champion_detailss)
        ],
        bucket_name=env.MINIO_BUCKET_NAME,
        object_name=env.OUTPUT_FILE_NAME,
        minio_client=minio_client,
        logger=logger,
    )

    minio_fput_object(
        file_path=str(env.OUTPUT_DIR / env.TRACES_FILE_NAME),
        bucket_name=env.MINIO_BUCKET_NAME,
        object_name=env.TRACES_FILE_NAME,
        content_type="application/zip",
        minio_client=minio_client,
        logger=logger,
    )


if __name__ == "__main__":
    import uvloop

    uvloop.run(main())
