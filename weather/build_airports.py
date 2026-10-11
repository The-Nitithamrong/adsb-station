#!/usr/bin/env python3
"""build_airports.py — สร้าง weather/airports.csv (IATA → พิกัด + ประเทศ) จาก OurAirports.

ไม่ได้รันบน Pi — รันมือบนเครื่อง dev เมื่ออยากรีเฟรชตาราง (สนามบินเปิด/ปิด เปลี่ยนรหัส).
weather_fetch.py ใช้ตารางนี้แปลง route ในปฏิทิน (เช่น BKK-TPE) เป็นพิกัดของเมืองปลายทาง.

ที่มา: OurAirports (https://ourairports.com/data/) — public domain (Unlicense).
  git clone --depth 1 https://github.com/davidmegginson/ourairports-data /tmp/oa
  python3 weather/build_airports.py /tmp/oa/airports.csv > weather/airports.csv

คัดเฉพาะสนามบินที่มี IATA + เที่ยวบินประจำ (scheduled_service=yes) — เหลือ ~4,000 แถว.
พิกัดปัด 3 ตำแหน่ง (~110 ม.) — พยากรณ์ละเอียดสุดที่ใช้คือ grid 3 กม. ทศนิยมมากกว่านี้ไม่ได้อะไร.
"""
import csv
import sys

TYPES = ("large_airport", "medium_airport", "small_airport")


def main(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            iata = r["iata_code"].strip().upper()
            if len(iata) != 3 or r["scheduled_service"] != "yes" or r["type"] not in TYPES:
                continue
            rows.append((iata, round(float(r["latitude_deg"]), 3),
                         round(float(r["longitude_deg"]), 3), r["iso_country"]))
    rows.sort()
    out = csv.writer(sys.stdout, lineterminator="\n")
    out.writerow(["iata", "lat", "lon", "country"])
    out.writerows(rows)


if __name__ == "__main__":
    main(sys.argv[1])
