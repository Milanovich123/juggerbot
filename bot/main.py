from typing import Optional
from ares import AresBot
from ares.consts import UnitRole
from ares.behaviors.macro import Mining
from ares.behaviors.macro import BuildStructure
from ares.behaviors.macro import ExpansionController
from ares.behaviors.macro import GasBuildingController

from sc2 import maps
from sc2.bot_ai import BotAI
from sc2.data import Difficulty, Race
from sc2.ids.ability_id import AbilityId
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.main import run_game
from sc2.player import Bot, Computer
from sc2.position import Point2
from sc2.unit import Unit
from sc2.units import Units

class MyBot(AresBot):
    def __init__(self, game_step_override: Optional[int] = None):
        """Initiate custom bot

        Parameters
        ----------
        game_step_override :
            If provided, set the game_step to this value regardless of how it was
            specified elsewhere
        """
        super().__init__(game_step_override)

    # Get creep edge towards enemy base
    def get_location_towards_enemy_on_creep(self, unit: Unit) -> None | Point2:
        target1 = self.enemy_start_locations[0]
        target2 = self.game_info.map_center
        target = (target1 + target2) / 2
        creep_tile = self.mediator.get_closest_creep_tile(
            pos=unit.position.towards(target, 6)
        )

        if creep_tile:
            return creep_tile
        else:
            return

    async def on_step(self, iteration: int) -> None:
        await super(MyBot, self).on_step(iteration)
        larvae: Units = self.larva
        hq: Unit = self.townhalls.first
        enemy_pos = self.enemy_start_locations[0]
        time = self.time_formatted + " "
        clumping_distance = 7

        self.register_behavior(Mining())

        ### SCOUTING LOGIC ###

        # Scout with overseers
        overseer = self.units(UnitTypeId.OVERSEER)
        for os in overseer:
            # Scout with overseer to enemy base
            if os.is_idle:
                os.move(enemy_pos)
            # If enemy is detected nearby, stay at range
            enemy_nearby = self.enemy_units.closer_than(15, os)
            if enemy_nearby:
                closest_enemy = enemy_nearby.closest_to(os)
                os.move(os.position.towards(closest_enemy.position, -2))
            if os.health_percentage < 1 and enemy_nearby:
                # Retreat damaged overseer
                # Use the scouting ability before moving back
                os.move(os.position.towards(closest_enemy.position, -10))
                if os.energy >= 30:
                    os(AbilityId.SPAWNCHANGELING_SPAWNCHANGELING)

        # Spread out overlords
        for overlord in self.units(UnitTypeId.OVERLORD).idle:
            pos: Point2 = overlord.position.random_on_distance(5)
            enemy_nearby = self.enemy_units.closer_than(15, overlord)
            if enemy_nearby:
                # Retreat overlord
                closest_enemy = enemy_nearby.closest_to(overlord)
                overlord.move(overlord.position.towards(closest_enemy.position, -10))
            else:
                overlord.move(pos)

        
        ### ECONOMY AND WORKER MANAGEMENT ###

        # Saturate gas
        for a in self.gas_buildings:
            if a.assigned_harvesters < a.ideal_harvesters:
                w: Units = self.workers.closer_than(10, a)
                if w:
                    w.random.gather(a)

        # Send workers across bases
        await self.distribute_workers()

        ### QUEEN LOGIC ###

        # Get idle inject queens
        inject_queens = self.mediator.get_units_from_role(
            role=UnitRole.QUEEN_INJECT,
            unit_type=UnitTypeId.QUEEN
        )
        for queen in inject_queens.idle:
            if queen.energy >= 25:
                closest_townhall = self.townhalls.closest_to(queen)
                queen(AbilityId.EFFECT_INJECTLARVA, closest_townhall)

        # Get idle creep queens
        creep_queens = self.mediator.get_units_from_role(
            role=UnitRole.QUEEN_CREEP,
            unit_type=UnitTypeId.QUEEN
        )

        for queen in creep_queens.idle:
            if queen.energy >= 25:
                # Get nearest creep edge using CreepManager
                target_pos = self.mediator.find_nearby_creep_edge_position(
                    position=queen.position
                )
                if target_pos:
                    queen(AbilityId.BUILD_CREEPTUMOR, target_pos)
            else:
                pos = self.get_location_towards_enemy_on_creep(queen)
                # Clumping
                if pos and queen.position.distance_to(creep_queens.center) > 8:
                    queen.move((creep_queens.center + pos) / 2)
                elif pos:
                    queen.move(pos)

        ### BUILDING STRUCTURES ###

        # Build spawning pool
        if self.structures(UnitTypeId.SPAWNINGPOOL).amount + self.already_pending(UnitTypeId.SPAWNINGPOOL) == 0 and self.already_pending(UnitTypeId.HATCHERY) == 1:
            if self.can_afford(UnitTypeId.SPAWNINGPOOL):
                self.register_behavior(BuildStructure(
                    base_location=self.start_location, structure_id=UnitTypeId.SPAWNINGPOOL
                    ))

        # Upgrade to lair if spawning pool is complete
        if self.structures(UnitTypeId.SPAWNINGPOOL).ready and self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) > 0 and self.units(UnitTypeId.QUEEN).amount >= 1:
            if hq.is_idle and not self.townhalls(UnitTypeId.LAIR) and not self.already_pending(UnitTypeId.LAIR):
                if self.can_afford(UnitTypeId.LAIR):
                    hq.build(UnitTypeId.LAIR)

        # If lair is ready and we have no hydra den on the way: build hydra den
        if self.structures(UnitTypeId.SPAWNINGPOOL).ready and self.can_afford(UnitTypeId.HYDRALISKDEN):
            if self.structures(UnitTypeId.HYDRALISKDEN).amount + self.already_pending(UnitTypeId.HYDRALISKDEN) == 0:
                self.register_behavior(BuildStructure(
                    base_location=self.start_location, structure_id=UnitTypeId.HYDRALISKDEN
                    ))

        # If we dont have both extractors: build them
        if (
            self.structures(UnitTypeId.SPAWNINGPOOL)
            and self.can_afford(UnitTypeId.EXTRACTOR)
        ):
            if (self.gas_buildings.amount + self.already_pending(UnitTypeId.EXTRACTOR) == 0):
                self.register_behavior(
                    GasBuildingController(to_count=1)
                )
            elif (self.gas_buildings.amount + self.already_pending(UnitTypeId.EXTRACTOR) == 1 and self.supply_cap >= 33):
                self.register_behavior(
                    GasBuildingController(to_count=len(self.townhalls))
                )

        ### UPGRADE LOGIC ###

        # Once the pool is done
        if self.structures(UnitTypeId.SPAWNINGPOOL).ready:
            # Upgrade zergling speed
            if self.can_afford(UpgradeId.ZERGLINGMOVEMENTSPEED) and self.already_pending_upgrade(UpgradeId.ZERGLINGMOVEMENTSPEED) == 0:
                self.research(UpgradeId.ZERGLINGMOVEMENTSPEED)
            # Build queen 
            elif not self.units(UnitTypeId.QUEEN).amount == self.townhalls.amount and hq.is_idle:
                if self.can_afford(UnitTypeId.QUEEN):
                    hq.train(UnitTypeId.QUEEN)
        
        # Once the hydra den is done
        den = self.structures(UnitTypeId.HYDRALISKDEN)
        if den.ready and den.idle:
            # Upgrade hydra range
            if self.can_afford(UpgradeId.EVOLVEGROOVEDSPINES) and self.already_pending_upgrade(UpgradeId.EVOLVEGROOVEDSPINES) == 0:
                self.research(UpgradeId.EVOLVEGROOVEDSPINES)
            # Upgrade hydra speed
            elif self.can_afford(UpgradeId.EVOLVEMUSCULARAUGMENTS) and self.already_pending_upgrade(UpgradeId.EVOLVEMUSCULARAUGMENTS) == 0:
                self.research(UpgradeId.EVOLVEMUSCULARAUGMENTS)

        ### TRAINING UNITS ###

        # Drone production logic
        # If we have exactly 13 drones, build an extra overlord
        if self.supply_workers + self.already_pending(UnitTypeId.DRONE) == 13 and not self.already_pending(UnitTypeId.OVERLORD):
            # Build an extra overlord at 13 drones
            if self.can_afford(UnitTypeId.OVERLORD):
                larvae.random.train(UnitTypeId.OVERLORD)
        # If we have 16 drones, build expansion
        elif self.supply_workers + self.already_pending(UnitTypeId.DRONE) == 16 and not self.already_pending(UnitTypeId.HATCHERY):
            self.register_behavior(
                ExpansionController(to_count=2, can_afford_check=False)
            )
        # If we have less than 38 drones, build drones
        elif self.supply_workers + self.already_pending(UnitTypeId.DRONE) < 38:
            if larvae and self.can_afford(UnitTypeId.DRONE):
                larva: Unit = larvae.random
                larva.train(UnitTypeId.DRONE)

        ## Overlord production logic
        # If supply is low, train overlords
        if (
            self.supply_left < 2
            and larvae
            and self.supply_cap < 200
            and self.can_afford(UnitTypeId.OVERLORD)
            and not self.already_pending(UnitTypeId.OVERLORD)
        ):
            larvae.random.train(UnitTypeId.OVERLORD)
        # If supply cap over 30 we can train multiple overlords
        if (
            self.supply_left <= 5
            and larvae
            and self.supply_cap > 30 
            and self.supply_cap < 200
            and self.can_afford(UnitTypeId.OVERLORD)
            and not self.already_pending(UnitTypeId.OVERLORD) > 1
        ):
            larvae.random.train(UnitTypeId.OVERLORD)

        # Extra queen when high on minerals and idle townhall
        if (
            hq.is_idle
            and self.structures(UnitTypeId.SPAWNINGPOOL).ready
            and self.minerals > 300
        ):
            hq.train(UnitTypeId.QUEEN)

        # Train zerglings
        if larvae and self.can_afford(UnitTypeId.HYDRALISK) and self.structures(UnitTypeId.HYDRALISKDEN).ready:
            larvae.random.train(UnitTypeId.HYDRALISK)
        elif larvae and self.can_afford(UnitTypeId.ZERGLING) and self.structures(UnitTypeId.SPAWNINGPOOL).ready:
            larvae.random.train(UnitTypeId.ZERGLING)

        # Morph overseer after lair
        if self.townhalls(UnitTypeId.LAIR).ready:
            if (
                self.can_afford(UnitTypeId.OVERSEER)
                and not self.already_pending(UnitTypeId.OVERSEER)
                and self.units(UnitTypeId.OVERSEER).amount < 1
            ):
                for ov in self.units(UnitTypeId.OVERLORD):
                    ov(AbilityId.MORPH_OVERSEER)
                    break


        ### ATTACK LOGIC ###

        # Defending force
        defenders: Units = self.mediator.get_units_from_role(
            role=UnitRole.DEFENDING,
        )

        # Drone under attack: pull drones to defend TODO: improve to not chase too long
        for drone in self.units(UnitTypeId.DRONE):
            enemy_nearby = self.enemy_units.closer_than(3, drone)
            if enemy_nearby:
                closest_enemy = enemy_nearby.closest_to(drone)
                drone.attack(closest_enemy)
                for unit in defenders:
                    unit.attack(closest_enemy)
       
       # Defend with lings and hydras
        if defenders:
            enemy_nearby = self.enemy_units.closer_than(15, defenders.center)
            if enemy_nearby:
                for unit in defenders:
                    closest_enemy = enemy_nearby.closest_to(unit)
                    unit.attack(closest_enemy)
            else:
                for unit in defenders:
                    if unit.position.distance_to(defenders.center) > clumping_distance:
                        unit.move(defenders.center)  
                    else:
                        pos = self.get_location_towards_enemy_on_creep(unit)
                        if pos:
                            unit.move(pos)

        # Attack with lings and hydras if we have enough
        if len(defenders) > 24:
            self.mediator.switch_roles(from_role=UnitRole.DEFENDING, to_role=UnitRole.ATTACKING)
        attacking_units: Units = self.mediator.get_units_from_role(
            role=UnitRole.ATTACKING,
        )
        if attacking_units:
            enemy_nearby = self.enemy_units.closer_than(20, attacking_units.center)
            if enemy_nearby:
                for unit in attacking_units:
                    closest_enemy = enemy_nearby.closest_to(unit)
                    unit.attack(closest_enemy)
            else:
                if attacking_units.amount > 6:
                    for unit in attacking_units:
                        if unit.position.distance_to(attacking_units.center) > clumping_distance:
                            unit.move(attacking_units.center)  
                        else:
                            unit.attack(enemy_pos)
                else:
                    for unit in attacking_units:
                        unit.move(attacking_units.center)  

        # If all our townhalls are dead, send all our units to attack
        if not self.townhalls:
            for unit in self.units.of_type({UnitTypeId.DRONE, UnitTypeId.QUEEN, UnitTypeId.ZERGLING}):
                unit.attack(enemy_pos)
        
    async def on_unit_created(self, unit: Unit) -> None:
        await super(MyBot, self).on_unit_created(unit)

        if unit.type_id == UnitTypeId.ZERGLING:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.DEFENDING)
        
        if unit.type_id == UnitTypeId.HYDRALISK:
            self.mediator.assign_role(tag=unit.tag, role=UnitRole.DEFENDING)

        if unit.type_id == UnitTypeId.QUEEN:
            queens = self.units(UnitTypeId.QUEEN)
            if queens.amount > self.townhalls.amount:
                self.mediator.assign_role(tag=unit.tag, role=UnitRole.QUEEN_CREEP)
            else:
                self.mediator.assign_role(tag=unit.tag, role=UnitRole.QUEEN_INJECT)


    # async def on_start(self) -> None:
    #     await super(MyBot, self).on_start()
    #
    #     # on_start logic here ...
    #
    # async def on_end(self, game_result: Result) -> None:
    #     await super(MyBot, self).on_end(game_result)
    #
    #     # custom on_end logic here ...
    #
    # async def on_building_construction_complete(self, unit: Unit) -> None:
    #     await super(MyBot, self).on_building_construction_complete(unit)
    #
    #     # custom on_building_construction_complete logic here ...
    #

    #
    # async def on_unit_destroyed(self, unit_tag: int) -> None:
    #     await super(MyBot, self).on_unit_destroyed(unit_tag)
    #
    #     # custom on_unit_destroyed logic here ...
    #
    # async def on_unit_took_damage(self, unit: Unit, amount_damage_taken: float) -> None:
    #     await super(MyBot, self).on_unit_took_damage(unit, amount_damage_taken)
    #
    #     # custom on_unit_took_damage logic here ...
