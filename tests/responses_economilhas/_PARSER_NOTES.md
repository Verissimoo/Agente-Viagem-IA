# _PARSER_NOTES.md — caminhos do `data` por programa que retornou voo

Gerado em: 2026-05-05T18:53:40

## SMILES — amostra de `internacional_BSB_LIS`

Top-level keys:
```
hasCalendar, calendarStatus, requestedFlightSegmentList, isSmFlexActive, resultType, hasG3, hasCongener, tripTypeRequest, pricingChannelList, hasAvianca, passenger
```
Hierarquia (até profundidade 4):
```
hasCalendar: bool
calendarStatus: str
requestedFlightSegmentList: list(1)
requestedFlightSegmentList.type: str
requestedFlightSegmentList.flightList: list(0)
requestedFlightSegmentList.airports: dict(2)
requestedFlightSegmentList.airports.departureAirportList: list(0)
requestedFlightSegmentList.airports.arrivalAirportList: list(0)
requestedFlightSegmentList.companyList: list(0)
requestedFlightSegmentList.cabinList: list(0)
requestedFlightSegmentList.calendarDayList: list(6)
requestedFlightSegmentList.calendarDayList.date: str
requestedFlightSegmentList.bestPricing: dict(0)
isSmFlexActive: bool
resultType: str
hasG3: bool
hasCongener: bool
tripTypeRequest: str
pricingChannelList: NoneType
hasAvianca: bool
passenger: dict(3)
passenger.adults: str
passenger.children: str
passenger.infants: str
```

## LATAM — amostra de `internacional_BSB_LIS`

Top-level keys:
```
outbound, inbound
```
Hierarquia (até profundidade 4):
```
outbound: dict(13)
outbound.pageable: dict(6)
outbound.pageable.pageNumber: int
outbound.pageable.pageSize: int
outbound.pageable.sort: list(1)
outbound.pageable.sort.direction: str
outbound.pageable.sort.property: str
outbound.pageable.sort.ignoreCase: bool
outbound.pageable.sort.nullHandling: str
outbound.pageable.sort.descending: bool
outbound.pageable.sort.ascending: bool
outbound.pageable.offset: int
outbound.pageable.paged: bool
outbound.pageable.unpaged: bool
outbound.totalPages: int
outbound.totalElements: int
outbound.last: bool
outbound.numberOfElements: int
outbound.first: bool
outbound.sort: list(1)
outbound.sort.direction: str
outbound.sort.property: str
outbound.sort.ignoreCase: bool
outbound.sort.nullHandling: str
outbound.sort.descending: bool
outbound.sort.ascending: bool
outbound.size: int
outbound.number: int
outbound.empty: bool
outbound.content: list(42)
outbound.content.summary: dict(13)
outbound.content.summary.tags: list(2)
outbound.content.summary.stopOvers: int
outbound.content.summary.duration: int
outbound.content.summary.flightCode: str
outbound.content.summary.origin: dict(5)
outbound.content.summary.origin.departure: str
outbound.content.summary.origin.departureTime: str
outbound.content.summary.origin.iataCode: str
outbound.content.summary.origin.airport: str
outbound.content.summary.origin.city: str
outbound.content.summary.destination: dict(5)
outbound.content.summary.destination.arrival: str
outbound.content.summary.destination.arrivalTime: str
outbound.content.summary.destination.iataCode: str
outbound.content.summary.destination.airport: str
outbound.content.summary.destination.city: str
outbound.content.summary.brands: list(5)
outbound.content.summary.brands.id: str
outbound.content.summary.brands.offerId: str
outbound.content.summary.brands.brandText: str
outbound.content.summary.brands.brandDescription: str
outbound.content.summary.brands.promotionalText: dict(2)
outbound.content.summary.brands.cabin: dict(2)
outbound.content.summary.brands.rules: dict(0)
outbound.content.summary.brands.farebasis: str
outbound.content.summary.brands.price: dict(7)
outbound.content.summary.brands.lowestPriceDifference: dict(6)
outbound.content.summary.brands.lowestPriceBrand: str
outbound.content.summary.brands.messages: list(0)
```

## AZUL — amostra de `internacional_BSB_LIS`

Top-level keys:
```
data, notifications
```
Hierarquia (até profundidade 4):
```
data: dict(5)
data.companionPass: bool
data.flowNotifications: NoneType
data.emergencyBanners: list(1)
data.emergencyBanners.message: str
data.discountType: str
data.trips: list(1)
data.trips.departureStation: str
data.trips.arrivalStation: str
data.trips.std: str
data.trips.currencyCode: str
data.trips.flightType: str
data.trips.region: str
data.trips.fareInformation: dict(2)
data.trips.fareInformation.lowestPoints: float
data.trips.fareInformation.highestPoints: float
data.trips.journeys: list(5)
data.trips.journeys.journeyKey: str
data.trips.journeys.journeySellKey: str
data.trips.journeys.restrictions: NoneType
data.trips.journeys.status: dict(2)
data.trips.journeys.status.available: bool
data.trips.journeys.status.reason: NoneType
data.trips.journeys.fares: list(2)
data.trips.journeys.fares.available: bool
data.trips.journeys.fares.reason: NoneType
data.trips.journeys.fares.productClass: dict(3)
data.trips.journeys.fares.cabin: dict(2)
data.trips.journeys.fares.classOfService: str
data.trips.journeys.fares.key: str
data.trips.journeys.fares.fareSellKey: str
data.trips.journeys.fares.paxPoints: list(5)
data.trips.journeys.fares.paxFares: NoneType
data.trips.journeys.identifier: dict(10)
data.trips.journeys.identifier.operatedBy: str
data.trips.journeys.identifier.carrierCode: str
data.trips.journeys.identifier.flightNumber: str
data.trips.journeys.identifier.opSuffix: NoneType
data.trips.journeys.identifier.departureStation: str
data.trips.journeys.identifier.arrivalStation: str
data.trips.journeys.identifier.std: str
data.trips.journeys.identifier.sta: str
data.trips.journeys.identifier.duration: dict(3)
data.trips.journeys.identifier.connections: dict(2)
data.trips.journeys.segments: list(2)
data.trips.journeys.segments.equipment: dict(3)
data.trips.journeys.segments.segmentKey: str
data.trips.journeys.segments.identifier: dict(9)
data.trips.journeys.segments.legs: list(1)
notifications: list(0)
```

## IBERIA — amostra de `internacional_BSB_LIS`

Top-level keys:
```
outbound, inbound
```
Hierarquia (até profundidade 4):
```
outbound: list(0)
inbound: NoneType
```

## BRITISH — amostra de `internacional_BSB_LIS`

Top-level keys:
```
outbound, inbound
```
Hierarquia (até profundidade 4):
```
outbound: list(0)
inbound: NoneType
```

## COPA — amostra de `internacional_GRU_MIA`

Top-level keys:
```
originDestinations
```
Hierarquia (até profundidade 4):
```
originDestinations: list(1)
originDestinations.originDestinationKey: str
originDestinations.promoCodeApplied: bool
originDestinations.discountType: NoneType
originDestinations.origin: dict(2)
originDestinations.origin.code: str
originDestinations.origin.departureDate: str
originDestinations.destination: dict(3)
originDestinations.destination.code: str
originDestinations.destination.destinationImageURL: str
originDestinations.destination.nearbyAirports: list(1)
originDestinations.currency: dict(2)
originDestinations.currency.code: str
originDestinations.currency.decimals: int
originDestinations.priceCalendars: list(7)
originDestinations.priceCalendars.date: str
originDestinations.priceCalendars.price: float
originDestinations.solutions: list(31)
originDestinations.solutions.key: str
originDestinations.solutions.numberOfLayovers: int
originDestinations.solutions.journeyTime: str
originDestinations.solutions.lowestPriceCoachCabin: dict(2)
originDestinations.solutions.lowestPriceCoachCabin.miles: int
originDestinations.solutions.lowestPriceCoachCabin.taxes: float
originDestinations.solutions.lowestPriceBusinessCabin: dict(2)
originDestinations.solutions.lowestPriceBusinessCabin.miles: int
originDestinations.solutions.lowestPriceBusinessCabin.taxes: int
originDestinations.solutions.originalLowestPriceCoachCabin: int
originDestinations.solutions.originalLowestPriceBusinessCabin: int
originDestinations.solutions.flights: list(2)
originDestinations.solutions.flights.flightKey: str
originDestinations.solutions.flights.flightRefKey: str
originDestinations.solutions.flights.layoverTime: str
originDestinations.solutions.flights.changeOfDay: str
originDestinations.solutions.flights.aircraftName: str
originDestinations.solutions.flights.marketingCarrier: dict(4)
originDestinations.solutions.flights.marketingCarrier.flightNumber: str
originDestinations.solutions.flights.marketingCarrier.airlineCode: str
originDestinations.solutions.flights.marketingCarrier.airlineName: str
originDestinations.solutions.flights.marketingCarrier.disclosure: NoneType
originDestinations.solutions.flights.operatingCarrier: NoneType
originDestinations.solutions.flights.arrival: dict(5)
originDestinations.solutions.flights.arrival.airportCode: str
originDestinations.solutions.flights.arrival.airportName: str
originDestinations.solutions.flights.arrival.flightDate: str
originDestinations.solutions.flights.arrival.flightTime: str
originDestinations.solutions.flights.arrival.terminal: NoneType
originDestinations.solutions.flights.departure: dict(5)
originDestinations.solutions.flights.departure.airportCode: str
originDestinations.solutions.flights.departure.airportName: str
originDestinations.solutions.flights.departure.flightDate: str
originDestinations.solutions.flights.departure.flightTime: str
originDestinations.solutions.flights.departure.terminal: str
originDestinations.solutions.flights.onTimePerformance: NoneType
originDestinations.solutions.flights.thruFlights: int
originDestinations.solutions.flights.stops: list(0)
originDestinations.solutions.flights.dreamsCabin: bool
originDestinations.solutions.offers: list(1)
originDestinations.solutions.offers.id: str
originDestinations.solutions.offers.pricePerAdult: dict(2)
```

## AZUL_INTERLINE — amostra de `domestica_GRU_REC`

Top-level keys:
```
origin, originAirportName, finalDestination, finalDestinationAirportName, passengers, departureFlights, returnFlights, pagination
```
Hierarquia (até profundidade 4):
```
origin: str
originAirportName: str
finalDestination: str
finalDestinationAirportName: str
passengers: dict(3)
passengers.adultNumber: int
passengers.childNumber: int
passengers.infantNumber: int
departureFlights: dict(2)
departureFlights.date: str
departureFlights.flights: list(0)
returnFlights: dict(2)
returnFlights.date: NoneType
returnFlights.flights: list(0)
pagination: dict(2)
pagination.page: int
pagination.isFinalPagination: bool
```

