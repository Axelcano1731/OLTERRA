"""Plan de direcciones del túnel.

Por qué 198.18.0.0/15 (RFC 2544, reservado para pruebas de rendimiento): los ISP
usan 10/8 para sus pools, 100.64/10 para CGNAT, 172.16/12 para gestión (ISPWatch
mismo usa 172.18.x en su overlay) y 192.168/16 en las OLT de fábrica. Este rango
casi nadie lo enruta internamente, así que no choca con lo que ya hay en el
MikroTik del ISP. Se puede cambiar por configuración.

- ``platform_prefix`` (198.18.0.0/24): el concentrador y los ejecutores.
- ``peer_pool`` (198.18.0.0/16): un bloque /28 por tenant para sus routers.
- ``nat_pool`` (198.19.0.0/16): un bloque /26 por tenant; cada OLT recibe una IP
  única ahí (NAT 1:1), así dos ISP con la OLT en 192.168.8.200 no chocan.

Cada tenant tiene un ``net_index`` global y sus bloques se derivan de él. Eso
evita buscar "la siguiente IP libre" mirando a otros tenants, que con RLS ni
siquiera se pueden ver: las colisiones son imposibles por construcción.
"""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network


class AddressPlanError(ValueError):
    pass


@dataclass(frozen=True)
class AddressPlan:
    platform_prefix: IPv4Network
    peer_pool: IPv4Network
    peer_block_prefixlen: int
    nat_pool: IPv4Network
    nat_block_prefixlen: int

    @classmethod
    def from_strings(
        cls,
        platform_prefix: str = "198.18.0.0/24",
        peer_pool: str = "198.18.0.0/16",
        peer_block_prefixlen: int = 28,
        nat_pool: str = "198.19.0.0/16",
        nat_block_prefixlen: int = 26,
    ) -> AddressPlan:
        plan = cls(
            IPv4Network(platform_prefix),
            IPv4Network(peer_pool),
            peer_block_prefixlen,
            IPv4Network(nat_pool),
            nat_block_prefixlen,
        )
        plan.validate()
        return plan

    def validate(self) -> None:
        if self.peer_pool.overlaps(self.nat_pool):
            raise AddressPlanError("El pool de routers y el de NAT no pueden solaparse")
        if not self.platform_prefix.subnet_of(self.peer_pool):
            raise AddressPlanError("El prefijo de la plataforma va dentro del pool de routers")
        if not self.peer_pool.prefixlen < self.peer_block_prefixlen <= 30:
            raise AddressPlanError("Bloque de routers inválido")
        if not self.nat_pool.prefixlen < self.nat_block_prefixlen <= 32:
            raise AddressPlanError("Bloque de NAT inválido")

    # --- Bloques por tenant -----------------------------------------------------

    def _skip_blocks(self) -> int:
        """Bloques del pool de routers ocupados por el prefijo de la plataforma."""
        block_size = 2 ** (32 - self.peer_block_prefixlen)
        offset = int(self.platform_prefix.network_address) - int(self.peer_pool.network_address)
        if offset != 0:
            raise AddressPlanError("El prefijo de la plataforma debe estar al inicio del pool")
        return -(-self.platform_prefix.num_addresses // block_size)

    @property
    def max_tenants(self) -> int:
        peer_blocks = (
            2 ** (self.peer_block_prefixlen - self.peer_pool.prefixlen) - self._skip_blocks()
        )
        nat_blocks = 2 ** (self.nat_block_prefixlen - self.nat_pool.prefixlen)
        return min(peer_blocks, nat_blocks)

    def _check_index(self, net_index: int) -> None:
        if not 1 <= net_index <= self.max_tenants:
            raise AddressPlanError(f"net_index fuera de rango (1..{self.max_tenants})")

    def tenant_peer_block(self, net_index: int) -> IPv4Network:
        self._check_index(net_index)
        block_size = 2 ** (32 - self.peer_block_prefixlen)
        start = (
            int(self.peer_pool.network_address) + (self._skip_blocks() + net_index - 1) * block_size
        )
        return IPv4Network((start, self.peer_block_prefixlen))

    def tenant_nat_block(self, net_index: int) -> IPv4Network:
        self._check_index(net_index)
        block_size = 2 ** (32 - self.nat_block_prefixlen)
        start = int(self.nat_pool.network_address) + (net_index - 1) * block_size
        return IPv4Network((start, self.nat_block_prefixlen))

    # --- Direcciones -----------------------------------------------------------------

    def peer_capacity(self) -> int:
        return 2 ** (32 - self.peer_block_prefixlen) - 2

    def nat_capacity(self) -> int:
        return 2 ** (32 - self.nat_block_prefixlen)

    def peer_address(self, net_index: int, peer_index: int) -> IPv4Address:
        """Dirección del router ``peer_index`` (1..n) del tenant en el overlay."""
        if not 1 <= peer_index <= self.peer_capacity():
            raise AddressPlanError(f"Un tenant admite hasta {self.peer_capacity()} routers")
        return self.tenant_peer_block(net_index).network_address + peer_index

    def nat_address(self, net_index: int, olt_index: int) -> IPv4Address:
        """IP única de la OLT ``olt_index`` (0..n-1) del tenant."""
        if not 0 <= olt_index < self.nat_capacity():
            raise AddressPlanError(f"Un tenant admite hasta {self.nat_capacity()} OLT")
        return self.tenant_nat_block(net_index).network_address + olt_index
