//+------------------------------------------------------------------+
//|  PolybotBridgeEA.mq5  –  Polybot MT5 Bridge Expert Advisor      |
//|  Sends OHLCV bars + tick + account + positions to Python server  |
//|  and executes trade commands received back.                      |
//+------------------------------------------------------------------+
#property copyright "Polybot"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>

input string   InpServerURL   = "https://polybot.mssmart.id";  // Python bridge URL
input string   InpSymbol      = "";                          // Symbol (blank = current)
input ENUM_TIMEFRAMES InpTF   = PERIOD_M15;                 // SR timeframe
input int      InpBars        = 300;                         // Bars to send
input int      InpSleepMs     = 1000;                        // Poll interval ms
input bool     InpDrawVisuals = true;                        // Gambar SR zone + Fibonacci
input int      InpDrawIntervalSec = 30;                      // Update visual setiap N detik
input color    InpSupportColor    = clrDodgerBlue;           // Warna support zone
input color    InpResistColor     = clrTomato;               // Warna resistance zone
input color    InpFibColor        = clrGold;                 // Warna garis Fibonacci
input color    InpFibExtColor     = clrMediumPurple;         // Warna garis Fib extension
input int      InpZoneTransparency = 60;                     // Transparansi fill kotak: 0=invisible 255=solid
input color    InpEMAFastColor    = clrDodgerBlue;           // Warna EMA fast (20)
input color    InpEMASlowColor    = clrOrangeRed;            // Warna EMA slow (50)
input color    InpSwingColor      = clrYellow;               // Warna dot swing H/L
input color    InpDailyHLColor    = clrSilver;               // Warna daily high/low

CTrade trade;
string g_symbol;
datetime g_last_draw = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   g_symbol = (InpSymbol == "") ? _Symbol : InpSymbol;
   trade.SetExpertMagicNumber(202500);
   EventSetMillisecondTimer(InpSleepMs);
   Print("[PolybotBridgeEA] Initialized. Symbol=", g_symbol,
         " Server=", InpServerURL);
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
   EventKillTimer();
   DeleteDrawObjects();
   ChartRedraw();
}

//+------------------------------------------------------------------+
void OnChartEvent(const int id, const long& lparam, const double& dparam, const string& sparam)
{
   // Redraw otomatis saat periode/zoom chart berubah
   if (id == CHARTEVENT_CHART_CHANGE && InpDrawVisuals)
   {
      g_last_draw = 0;   // paksa redraw di next tick
      PollDraw();
   }
}

//+------------------------------------------------------------------+
void OnTimer()
{
   if (!TerminalInfoInteger(TERMINAL_CONNECTED)) return;

   PushRates();
   PollCommand();

   // Update visual setiap InpDrawIntervalSec detik
   if (InpDrawVisuals && TimeCurrent() - g_last_draw >= InpDrawIntervalSec) {
      PollDraw();
      g_last_draw = TimeCurrent();
   }
}

//+------------------------------------------------------------------+
void RegisterSymbolMeta()
{
   int digits   = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(g_symbol,  SYMBOL_POINT);
   double csize = SymbolInfoDouble(g_symbol,  SYMBOL_TRADE_CONTRACT_SIZE);
   int spread   = (int)SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);

   string body = StringFormat(
      "{\"symbol\":\"%s\",\"digits\":%d,\"point\":%.10f,"
      "\"contract_size\":%.2f,\"spread\":%d}",
      g_symbol, digits, point, csize, spread
   );

   HttpPost(InpServerURL + "/ea/symbol-meta", body);
}

//+------------------------------------------------------------------+
void PushRates()
{
   MqlRates bars[];
   int copied = CopyRates(g_symbol, InpTF, 0, InpBars, bars);
   if (copied <= 0) return;

   MqlTick tick;
   if (!SymbolInfoTick(g_symbol, tick)) return;

   // Build bars JSON
   string barsJson = "[";
   for (int i = 0; i < copied; i++) {
      if (i > 0) barsJson += ",";
      barsJson += StringFormat(
         "{\"time\":%d,\"open\":%.5f,\"high\":%.5f,\"low\":%.5f,\"close\":%.5f,\"volume\":%d}",
         (long)bars[i].time, bars[i].open, bars[i].high,
         bars[i].low, bars[i].close, (long)bars[i].tick_volume
      );
   }
   barsJson += "]";

   // Tick JSON
   string tickJson = StringFormat(
      "{\"symbol\":\"%s\",\"bid\":%.5f,\"ask\":%.5f,\"time\":%d}",
      g_symbol, tick.bid, tick.ask, (long)tick.time
   );

   // Account JSON
   string accType = (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO) ? "Demo" : "Real";
   string accJson = StringFormat(
      "{\"login\":%d,\"balance\":%.2f,\"equity\":%.2f,"
      "\"margin\":%.2f,\"free_margin\":%.2f,\"profit\":%.2f,\"currency\":\"%s\","
      "\"broker\":\"%s\",\"server\":\"%s\",\"leverage\":%d,\"account_type\":\"%s\","
      "\"margin_level\":%.2f}",
      (long)AccountInfoInteger(ACCOUNT_LOGIN),
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      AccountInfoDouble(ACCOUNT_MARGIN),
      AccountInfoDouble(ACCOUNT_MARGIN_FREE),
      AccountInfoDouble(ACCOUNT_PROFIT),
      AccountInfoString(ACCOUNT_CURRENCY),
      AccountInfoString(ACCOUNT_COMPANY),
      AccountInfoString(ACCOUNT_SERVER),
      (int)AccountInfoInteger(ACCOUNT_LEVERAGE),
      accType,
      AccountInfoDouble(ACCOUNT_MARGIN) > 0
         ? AccountInfoDouble(ACCOUNT_EQUITY) / AccountInfoDouble(ACCOUNT_MARGIN) * 100
         : 0.0
   );

   // Positions JSON
   string posJson = "[";
   int total = PositionsTotal();
   for (int i = 0; i < total; i++) {
      ulong ticket = PositionGetTicket(i);
      if (ticket == 0) continue;
      if (i > 0) posJson += ",";
      string ptype = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? "buy" : "sell";
      posJson += StringFormat(
         "{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
         "\"volume\":%.2f,\"price_open\":%.5f,\"sl\":%.5f,\"tp\":%.5f,"
         "\"profit\":%.2f,\"comment\":\"%s\"}",
         (long)ticket,
         PositionGetString(POSITION_SYMBOL),
         ptype,
         PositionGetDouble(POSITION_VOLUME),
         PositionGetDouble(POSITION_PRICE_OPEN),
         PositionGetDouble(POSITION_SL),
         PositionGetDouble(POSITION_TP),
         PositionGetDouble(POSITION_PROFIT),
         PositionGetString(POSITION_COMMENT)
      );
   }
   posJson += "]";

   // Meta JSON (dikirim setiap tick agar server restart langsung kenal symbol)
   int    digits   = (int)SymbolInfoInteger(g_symbol, SYMBOL_DIGITS);
   double point    = SymbolInfoDouble(g_symbol,  SYMBOL_POINT);
   double csize    = SymbolInfoDouble(g_symbol,  SYMBOL_TRADE_CONTRACT_SIZE);
   int    spread   = (int)SymbolInfoInteger(g_symbol, SYMBOL_SPREAD);
   string metaJson = StringFormat(
      "{\"symbol\":\"%s\",\"digits\":%d,\"point\":%.10f,"
      "\"contract_size\":%.2f,\"spread\":%d}",
      g_symbol, digits, point, csize, spread
   );

   string tfStr = TimeframeToString(InpTF);
   string body = StringFormat(
      "{\"symbol\":\"%s\",\"timeframe\":\"%s\","
      "\"bars\":%s,\"tick\":%s,\"meta\":%s,\"account\":%s,\"positions\":%s}",
      g_symbol, tfStr, barsJson, tickJson, metaJson, accJson, posJson
   );

   HttpPost(InpServerURL + "/ea/rates", body);
}

//+------------------------------------------------------------------+
void PollCommand()
{
   string url = InpServerURL + "/ea/command?symbol=" + g_symbol;
   string resp = HttpGet(url);
   if (resp == "" || StringFind(resp, "\"NONE\"") >= 0) return;

   // Parse action
   string action = JsonGetString(resp, "action");
   double lot    = JsonGetDouble(resp, "lot");
   double sl     = JsonGetDouble(resp, "sl");
   double tp     = JsonGetDouble(resp, "tp");
   string comment = JsonGetString(resp, "comment");

   if (action == "BUY") {
      double ask = SymbolInfoDouble(g_symbol, SYMBOL_ASK);
      trade.Buy(lot, g_symbol, ask, sl, tp, comment);
      Print("[PolybotBridgeEA] BUY executed lot=", lot, " sl=", sl, " tp=", tp);

   } else if (action == "SELL") {
      double bid = SymbolInfoDouble(g_symbol, SYMBOL_BID);
      trade.Sell(lot, g_symbol, bid, sl, tp, comment);
      Print("[PolybotBridgeEA] SELL executed lot=", lot, " sl=", sl, " tp=", tp);

   } else if (action == "MODIFY_SL") {
      long ticket = (long)JsonGetDouble(resp, "ticket");
      if (PositionSelectByTicket(ticket)) {
         double cur_tp = PositionGetDouble(POSITION_TP);
         trade.PositionModify(ticket, sl, cur_tp);
         Print("[PolybotBridgeEA] MODIFY_SL ticket=", ticket, " sl=", sl, " comment=", comment);
      }

   } else if (action == "PARTIAL_CLOSE") {
      long ticket = (long)JsonGetDouble(resp, "ticket");
      if (PositionSelectByTicket(ticket)) {
         trade.PositionClosePartial(ticket, lot);
         Print("[PolybotBridgeEA] PARTIAL_CLOSE ticket=", ticket, " lot=", lot);
      }

   } else if (action == "CLOSE") {
      long ticket = (long)JsonGetDouble(resp, "ticket");
      if (PositionSelectByTicket(ticket)) {
         trade.PositionClose(ticket);
         Print("[PolybotBridgeEA] CLOSE ticket=", ticket, " comment=", comment);
      }

   } else if (action == "CLOSE_ALL") {
      CloseAll();
   }
}

//+------------------------------------------------------------------+
void CloseAll()
{
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if (ticket > 0 && PositionGetString(POSITION_SYMBOL) == g_symbol)
         trade.PositionClose(ticket);
   }
}

//+------------------------------------------------------------------+
// Visual Drawing — SR Zones + Fibonacci
//+------------------------------------------------------------------+

void PollDraw()
{
   string url  = InpServerURL + "/ea/draw?symbol=" + g_symbol;
   string resp = HttpGet(url);
   if (resp == "") return;

   DeleteDrawObjects();
   DrawSRZones(resp);
   DrawFibLevels(resp);
   DrawEMALines();
   DrawSwingDots();
   DrawDailyHL();
   DrawInfoLabel();
   ChartRedraw();
}

void DeleteDrawObjects()
{
   // Hapus semua objek buatan bot ini sebelum redraw
   int total = ObjectsTotal(0);
   for (int i = total - 1; i >= 0; i--) {
      string name = ObjectName(0, i);
      if (StringFind(name, "PB_") == 0)
         ObjectDelete(0, name);
   }
}

void DrawSRZones(string json)
{
   // Parse array zones dari JSON
   // Format: "zones":[{"low":x,"high":x,"type":"support","strength":x},...]
   int zonesStart = StringFind(json, "\"zones\":[");
   if (zonesStart < 0) return;
   zonesStart += 9;
   int zonesEnd = StringFind(json, "]", zonesStart);
   if (zonesEnd < 0) return;

   string zonesJson = StringSubstr(json, zonesStart, zonesEnd - zonesStart);
   int idx = 0;
   int objCount = 0;
   // Gunakan PERIOD_CURRENT agar SR zone muncul di semua periode chart
   datetime t1 = iTime(g_symbol, PERIOD_CURRENT, 300);
   datetime t2 = iTime(g_symbol, PERIOD_CURRENT, 0) + PeriodSeconds(PERIOD_CURRENT) * 50;

   while (true) {
      int start = StringFind(zonesJson, "{", idx);
      if (start < 0) break;
      int end = StringFind(zonesJson, "}", start);
      if (end < 0) break;

      string zoneStr = StringSubstr(zonesJson, start, end - start + 1);
      double low     = JsonGetDouble(zoneStr, "low");
      double high    = JsonGetDouble(zoneStr, "high");
      string ztype   = JsonGetString(zoneStr, "type");
      double strength = JsonGetDouble(zoneStr, "strength");

      if (low > 0 && high > low) {
         string name = "PB_ZONE_" + IntegerToString(objCount);
         color  zcolor = (ztype == "support") ? InpSupportColor : InpResistColor;

         ObjectCreate(0, name, OBJ_RECTANGLE, 0, t1, high, t2, low);
         ObjectSetInteger(0, name, OBJPROP_COLOR, zcolor);
         ObjectSetInteger(0, name, OBJPROP_STYLE, STYLE_SOLID);
         ObjectSetInteger(0, name, OBJPROP_WIDTH, 2);
         ObjectSetInteger(0, name, OBJPROP_FILL, false);   // outline only = transparan
         ObjectSetInteger(0, name, OBJPROP_BACK, false);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);

         // Label di tepi kanan zone
         string lblName = "PB_ZONE_LBL_" + IntegerToString(objCount);
         string lblText = (ztype == "support" ? "S" : "R") +
                          IntegerToString((int)strength);
         datetime lblTime = iTime(g_symbol, PERIOD_CURRENT, 0) + PeriodSeconds(PERIOD_CURRENT) * 3;
         ObjectCreate(0, lblName, OBJ_TEXT, 0, lblTime, (high + low) / 2);
         ObjectSetString(0, lblName, OBJPROP_TEXT, lblText);
         ObjectSetInteger(0, lblName, OBJPROP_COLOR, zcolor);
         ObjectSetInteger(0, lblName, OBJPROP_FONTSIZE, 8);
         ObjectSetInteger(0, lblName, OBJPROP_SELECTABLE, false);

         objCount++;
      }
      idx = end + 1;
   }
}

void DrawFibLevels(string json)
{
   int fibStart = StringFind(json, "\"fib\":{");
   if (fibStart < 0) return;  // fib null atau null

   double swingHigh = JsonGetDouble(json, "swing_high");
   double swingLow  = JsonGetDouble(json, "swing_low");
   if (swingHigh <= 0 || swingLow <= 0) return;

   // Gambar swing high & low sebagai HLINE
   DrawFibHLine("PB_FIB_HIGH", swingHigh, "Swing High", InpFibColor, STYLE_DOT, 1);
   DrawFibHLine("PB_FIB_LOW",  swingLow,  "Swing Low",  InpFibColor, STYLE_DOT, 1);

   // Level retracement entry
   string retLevels[] = {"38", "50", "61"};
   string retLabels[] = {"Fib 38.2%", "Fib 50.0%", "Fib 61.8%"};
   for (int i = 0; i < 3; i++) {
      double price = JsonGetDoubleKey(json, "retracements", retLevels[i]);
      if (price <= 0) continue;
      DrawFibHLine("PB_FIB_RET_" + retLevels[i], price, retLabels[i], InpFibColor, STYLE_SOLID, 1);
   }

   // Level extension TP
   string extLevels[] = {"127", "161"};
   string extLabels[] = {"Ext 127.2%", "Ext 161.8%"};
   for (int i = 0; i < 2; i++) {
      double price = JsonGetDoubleKey(json, "extensions", extLevels[i]);
      if (price <= 0) continue;
      DrawFibHLine("PB_FIB_EXT_" + extLevels[i], price, extLabels[i], InpFibExtColor, STYLE_DASH, 2);
   }
}

void DrawFibHLine(string name, double price, string label, color clr, int style, int width)
{
   // OBJ_HLINE muncul di semua periode tanpa perlu timestamp
   ObjectCreate(0, name, OBJ_HLINE, 0, 0, price);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_STYLE, style);
   ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, true);
   ObjectSetString(0, name, OBJPROP_TOOLTIP, label + " " + DoubleToString(price, _Digits));

   // Label teks di kanan chart
   string lblName = name + "_LBL";
   datetime lblTime = iTime(g_symbol, PERIOD_CURRENT, 0) + PeriodSeconds(PERIOD_CURRENT) * 3;
   ObjectCreate(0, lblName, OBJ_TEXT, 0, lblTime, price);
   ObjectSetString(0, lblName, OBJPROP_TEXT, " " + label + " " + DoubleToString(price, _Digits));
   ObjectSetInteger(0, lblName, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lblName, OBJPROP_FONTSIZE, 7);
   ObjectSetInteger(0, lblName, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
// EMA50 High/Low Band (channel) — sesuai strategi bot
//+------------------------------------------------------------------+
void DrawEMALines()
{
   int bars = 200;
   double highs[], lows[];
   if (CopyHigh(g_symbol, PERIOD_CURRENT, 0, bars, highs) <= 0) return;
   if (CopyLow(g_symbol,  PERIOD_CURRENT, 0, bars, lows)  <= 0) return;
   ArraySetAsSeries(highs, true);
   ArraySetAsSeries(lows,  true);

   int total = ArraySize(highs);

   // EMA50 dari high (band atas) & EMA50 dari low (band bawah)
   double bandH[], bandL[];
   ArrayResize(bandH, total);
   ArrayResize(bandL, total);

   double k = 2.0 / (50 + 1);
   bandH[total-1] = highs[total-1];
   bandL[total-1] = lows[total-1];
   for (int i = total - 2; i >= 0; i--) {
      bandH[i] = highs[i] * k + bandH[i+1] * (1 - k);
      bandL[i] = lows[i]  * k + bandL[i+1] * (1 - k);
   }

   // Gambar band sebagai garis bersambung (segmen OBJ_TREND antar candle)
   int limit = MathMin(total, bars) - 1;
   for (int i = 0; i < limit; i++) {
      datetime t1 = iTime(g_symbol, PERIOD_CURRENT, i + 1);
      datetime t2 = iTime(g_symbol, PERIOD_CURRENT, i);

      // Band atas
      string nH = "PB_BANDH_" + IntegerToString(i);
      ObjectCreate(0, nH, OBJ_TREND, 0, t1, bandH[i+1], t2, bandH[i]);
      ObjectSetInteger(0, nH, OBJPROP_COLOR, InpEMAFastColor);
      ObjectSetInteger(0, nH, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, nH, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nH, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nH, OBJPROP_BACK, true);

      // Band bawah
      string nL = "PB_BANDL_" + IntegerToString(i);
      ObjectCreate(0, nL, OBJ_TREND, 0, t1, bandL[i+1], t2, bandL[i]);
      ObjectSetInteger(0, nL, OBJPROP_COLOR, InpEMASlowColor);
      ObjectSetInteger(0, nL, OBJPROP_WIDTH, 2);
      ObjectSetInteger(0, nL, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nL, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nL, OBJPROP_BACK, true);
   }

   // Label di candle terakhir
   datetime tNow = iTime(g_symbol, PERIOD_CURRENT, 0);
   string lblH = "PB_BANDH_LBL";
   ObjectCreate(0, lblH, OBJ_TEXT, 0, tNow, bandH[0]);
   ObjectSetString(0, lblH, OBJPROP_TEXT, " EMA50 High");
   ObjectSetInteger(0, lblH, OBJPROP_COLOR, InpEMAFastColor);
   ObjectSetInteger(0, lblH, OBJPROP_FONTSIZE, 7);
   ObjectSetInteger(0, lblH, OBJPROP_SELECTABLE, false);

   string lblL = "PB_BANDL_LBL";
   ObjectCreate(0, lblL, OBJ_TEXT, 0, tNow, bandL[0]);
   ObjectSetString(0, lblL, OBJPROP_TEXT, " EMA50 Low");
   ObjectSetInteger(0, lblL, OBJPROP_COLOR, InpEMASlowColor);
   ObjectSetInteger(0, lblL, OBJPROP_FONTSIZE, 7);
   ObjectSetInteger(0, lblL, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
// Swing High / Swing Low dots
//+------------------------------------------------------------------+
void DrawSwingDots()
{
   int bars = 100;
   double highs[], lows[];
   if (CopyHigh(g_symbol, PERIOD_CURRENT, 0, bars, highs) <= 0) return;
   if (CopyLow(g_symbol,  PERIOD_CURRENT, 0, bars, lows)  <= 0) return;
   ArraySetAsSeries(highs, true);
   ArraySetAsSeries(lows,  true);

   int count = 0;
   for (int i = 2; i < bars - 2; i++) {
      datetime t = iTime(g_symbol, PERIOD_CURRENT, i);

      // Swing High
      if (highs[i] > highs[i-1] && highs[i] > highs[i-2] &&
          highs[i] > highs[i+1] && highs[i] > highs[i+2]) {
         string name = "PB_SWH_" + IntegerToString(count);
         ObjectCreate(0, name, OBJ_TEXT, 0, t, highs[i]);
         ObjectSetString(0, name, OBJPROP_TEXT, "▲");
         ObjectSetInteger(0, name, OBJPROP_COLOR, InpSwingColor);
         ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_BACK, false);
         count++;
      }

      // Swing Low
      if (lows[i] < lows[i-1] && lows[i] < lows[i-2] &&
          lows[i] < lows[i+1] && lows[i] < lows[i+2]) {
         string name = "PB_SWL_" + IntegerToString(count);
         ObjectCreate(0, name, OBJ_TEXT, 0, t, lows[i]);
         ObjectSetString(0, name, OBJPROP_TEXT, "▼");
         ObjectSetInteger(0, name, OBJPROP_COLOR, InpSwingColor);
         ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
         ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, name, OBJPROP_BACK, false);
         count++;
      }
   }
}

//+------------------------------------------------------------------+
// Daily High / Daily Low lines
//+------------------------------------------------------------------+
void DrawDailyHL()
{
   double dayHighs[], dayLows[];
   if (CopyHigh(g_symbol, PERIOD_D1, 0, 1, dayHighs) <= 0) return;
   if (CopyLow(g_symbol,  PERIOD_D1, 0, 1, dayLows)  <= 0) return;

   double dHigh = dayHighs[0];
   double dLow  = dayLows[0];

   // Daily High
   ObjectCreate(0, "PB_DAY_HIGH", OBJ_HLINE, 0, 0, dHigh);
   ObjectSetInteger(0, "PB_DAY_HIGH", OBJPROP_COLOR, InpDailyHLColor);
   ObjectSetInteger(0, "PB_DAY_HIGH", OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, "PB_DAY_HIGH", OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, "PB_DAY_HIGH", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "PB_DAY_HIGH", OBJPROP_BACK, true);

   string lhName = "PB_DAY_HIGH_LBL";
   datetime t = iTime(g_symbol, PERIOD_CURRENT, 0) + PeriodSeconds(PERIOD_CURRENT) * 3;
   ObjectCreate(0, lhName, OBJ_TEXT, 0, t, dHigh);
   ObjectSetString(0, lhName, OBJPROP_TEXT, " Day High " + DoubleToString(dHigh, _Digits));
   ObjectSetInteger(0, lhName, OBJPROP_COLOR, InpDailyHLColor);
   ObjectSetInteger(0, lhName, OBJPROP_FONTSIZE, 7);
   ObjectSetInteger(0, lhName, OBJPROP_SELECTABLE, false);

   // Daily Low
   ObjectCreate(0, "PB_DAY_LOW", OBJ_HLINE, 0, 0, dLow);
   ObjectSetInteger(0, "PB_DAY_LOW", OBJPROP_COLOR, InpDailyHLColor);
   ObjectSetInteger(0, "PB_DAY_LOW", OBJPROP_STYLE, STYLE_DOT);
   ObjectSetInteger(0, "PB_DAY_LOW", OBJPROP_WIDTH, 1);
   ObjectSetInteger(0, "PB_DAY_LOW", OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, "PB_DAY_LOW", OBJPROP_BACK, true);

   string llName = "PB_DAY_LOW_LBL";
   ObjectCreate(0, llName, OBJ_TEXT, 0, t, dLow);
   ObjectSetString(0, llName, OBJPROP_TEXT, " Day Low " + DoubleToString(dLow, _Digits));
   ObjectSetInteger(0, llName, OBJPROP_COLOR, InpDailyHLColor);
   ObjectSetInteger(0, llName, OBJPROP_FONTSIZE, 7);
   ObjectSetInteger(0, llName, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
// Info Label — Trend + ATR + EMA di pojok kiri atas
//+------------------------------------------------------------------+
void DrawInfoLabel()
{
   // Hitung EMA20, EMA50, ATR dari candle terkini
   double closes[], highs[], lows[];
   int bars = 60;
   if (CopyClose(g_symbol, PERIOD_CURRENT, 0, bars, closes) <= 0) return;
   if (CopyHigh(g_symbol,  PERIOD_CURRENT, 0, bars, highs)  <= 0) return;
   if (CopyLow(g_symbol,   PERIOD_CURRENT, 0, bars, lows)   <= 0) return;
   ArraySetAsSeries(closes, true);
   ArraySetAsSeries(highs,  true);
   ArraySetAsSeries(lows,   true);

   int total = ArraySize(closes);
   // EMA50 band: bandH dari high, bandL dari low
   double bandH = highs[total-1], bandL = lows[total-1];
   double k = 2.0/(50+1);
   for (int i = total - 2; i >= 0; i--) {
      bandH = highs[i] * k + bandH * (1 - k);
      bandL = lows[i]  * k + bandL * (1 - k);
   }

   // ATR 14
   double atr = 0;
   int atrPeriod = 14;
   for (int i = 1; i <= atrPeriod && i < total; i++) {
      double tr = MathMax(highs[i] - lows[i],
                  MathMax(MathAbs(highs[i] - closes[i+1 < total ? i+1 : i]),
                          MathAbs(lows[i]  - closes[i+1 < total ? i+1 : i])));
      atr += tr;
   }
   atr /= atrPeriod;

   // Trend dari posisi candle terakhir close (index 1) terhadap band
   double lastClose = closes[1];
   string trend; color trendColor;
   if (lastClose > bandH)      { trend = "BUY ↑ (di atas band)";  trendColor = clrLime; }
   else if (lastClose < bandL) { trend = "SELL ↓ (di bawah band)"; trendColor = clrTomato; }
   else                        { trend = "NETRAL (dalam band)";    trendColor = clrGray; }

   string infoText = StringFormat(
      "Trend     : %s\nEMA50 High: %.3f\nEMA50 Low : %.3f\nClose     : %.3f\nATR       : %.3f",
      trend, bandH, bandL, lastClose, atr
   );

   string name = "PB_INFO_LABEL";
   if (ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetString(0, name, OBJPROP_TEXT, infoText);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 20);
   ObjectSetInteger(0, name, OBJPROP_COLOR, trendColor);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
}

double JsonGetDoubleKey(string json, string section, string key)
{
   // Cari nilai di dalam nested object: "section":{"key":value,...}
   string sectionSearch = "\"" + section + "\":{";
   int sStart = StringFind(json, sectionSearch);
   if (sStart < 0) return 0;
   sStart += StringLen(sectionSearch);
   int sEnd = StringFind(json, "}", sStart);
   if (sEnd < 0) return 0;
   string sectionStr = StringSubstr(json, sStart, sEnd - sStart);

   string keySearch = "\"" + key + "\":";
   int kPos = StringFind(sectionStr, keySearch);
   if (kPos < 0) return 0;
   kPos += StringLen(keySearch);
   int kEnd = kPos;
   while (kEnd < StringLen(sectionStr)) {
      ushort c = StringGetCharacter(sectionStr, kEnd);
      if (c == ',' || c == '}') break;
      kEnd++;
   }
   return StringToDouble(StringSubstr(sectionStr, kPos, kEnd - kPos));
}

//+------------------------------------------------------------------+
// HTTP helpers
//+------------------------------------------------------------------+
void HttpPost(string url, string body)
{
   char data[], result[];
   string headers = "Content-Type: application/json\r\n";
   StringToCharArray(body, data, 0, StringLen(body));
   string resHeaders;
   int code = WebRequest("POST", url, headers, 5000, data, result, resHeaders);
   if (code < 0)
      Print("[HTTP] POST error ", GetLastError(), " url=", url);
}

string HttpGet(string url)
{
   char data[], result[];
   string headers, resHeaders;
   int code = WebRequest("GET", url, headers, 5000, data, result, resHeaders);
   if (code < 0) return "";
   return CharArrayToString(result);
}

//+------------------------------------------------------------------+
// Minimal JSON helpers
//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
{
   string search = "\"" + key + "\":\"";
   int pos = StringFind(json, search);
   if (pos < 0) return "";
   pos += StringLen(search);
   int end = StringFind(json, "\"", pos);
   if (end < 0) return "";
   return StringSubstr(json, pos, end - pos);
}

double JsonGetDouble(string json, string key)
{
   string search = "\"" + key + "\":";
   int pos = StringFind(json, search);
   if (pos < 0) return 0.0;
   pos += StringLen(search);
   int end = pos;
   while (end < StringLen(json)) {
      ushort c = StringGetCharacter(json, end);
      if (c == ',' || c == '}') break;
      end++;
   }
   return StringToDouble(StringSubstr(json, pos, end - pos));
}

string TimeframeToString(ENUM_TIMEFRAMES tf)
{
   switch(tf) {
      case PERIOD_M1:  return "M1";
      case PERIOD_M5:  return "M5";
      case PERIOD_M15: return "M15";
      case PERIOD_M30: return "M30";
      case PERIOD_H1:  return "H1";
      case PERIOD_H4:  return "H4";
      case PERIOD_D1:  return "D1";
      default:         return "M15";
   }
}
//+------------------------------------------------------------------+
