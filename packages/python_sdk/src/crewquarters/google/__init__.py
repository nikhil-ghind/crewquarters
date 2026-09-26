"""Google connectors (Gmail read and owner alerts, Sheets) proxied by the broker."""

from crewquarters._transport import BrokerClient
from crewquarters.google.gmail import GmailClient
from crewquarters.google.sheets import SheetsClient


class GoogleClients:
    def __init__(self, transport: BrokerClient) -> None:
        self.gmail = GmailClient(transport)
        self.sheets = SheetsClient(transport)


__all__ = ["GmailClient", "GoogleClients", "SheetsClient"]
