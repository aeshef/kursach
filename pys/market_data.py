import tinkoff.invest
from tinkoff.invest import PortfolioRequest, PortfolioPosition, Client, RequestError, CandleInterval, HistoricCandle, \
    OrderType, OrderDirection, Quotation, InstrumentIdType, InstrumentStatus
from tinkoff.invest.services import InstrumentsService
from datetime import datetime
import pandas as pd
import os
import requests
import time
import zipfile
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor

class DataStorage:
    """Класс для управления хранением данных"""
    
    def __init__(self, base_directory):
        """
        Инициализация хранилища данных
        
        :param base_directory: Корневая директория для хранения данных
        """
        self.base_directory = base_directory
        self.raw_data_dir = os.path.join(base_directory, "raw_data")
        self.processed_data_dir = os.path.join(base_directory, "processed_data")
        
        # Создаем основные директории, если они не существуют
        os.makedirs(self.raw_data_dir, exist_ok=True)
        os.makedirs(self.processed_data_dir, exist_ok=True)
    
    def get_ticker_raw_path(self, ticker):
        """Получить путь к папке сырых данных для тикера"""
        path = os.path.join(self.raw_data_dir, ticker)
        os.makedirs(path, exist_ok=True)
        return path
    
    def get_ticker_processed_path(self, ticker):
        """Получить путь к папке обработанных данных для тикера"""
        path = os.path.join(self.processed_data_dir, ticker)
        os.makedirs(path, exist_ok=True)
        return path
    
    def store_raw_data(self, ticker, figi, year, content):
        """Сохранить сырые данные из API"""
        ticker_dir = self.get_ticker_raw_path(ticker)
        file_path = os.path.join(ticker_dir, f"{figi}_{year}.zip")
        
        with open(file_path, 'wb') as file:
            file.write(content)
        
        return file_path
    
    def extract_raw_data(self, ticker):
        """Распаковать все ZIP-файлы для тикера"""
        ticker_dir = self.get_ticker_raw_path(ticker)
        extracted_files = []
        
        for file in os.listdir(ticker_dir):
            if file.endswith(".zip"):
                zip_path = os.path.join(ticker_dir, file)
                extract_dir = os.path.join(ticker_dir, file.replace(".zip", ""))
                
                try:
                    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                        zip_ref.extractall(extract_dir)
                    
                    # Собираем пути к извлеченным CSV-файлам
                    for root, _, files in os.walk(extract_dir):
                        for csv_file in files:
                            if csv_file.endswith(".csv"):
                                extracted_files.append(os.path.join(root, csv_file))
                                
                except zipfile.BadZipFile:
                    print(f"Bad zip file: {zip_path}")
        
        return extracted_files
    
    def load_raw_csv_files(self, csv_files):
        """Загрузить данные из CSV-файлов в единый DataFrame"""
        dfs = []
        
        for file_path in csv_files:
            try:
                temp_df = pd.read_csv(file_path, sep=";",
                                      names=["date", "open", "close", "min", "max", "volume", "unused"], 
                                      header=None)
                
                # Удаляем неиспользуемую колонку и обрабатываем даты
                if 'unused' in temp_df.columns:
                    temp_df.drop('unused', axis=1, inplace=True)
                
                dfs.append(temp_df)
            except Exception as e:
                print(f"Error reading {file_path}: {e}")
        
        if not dfs:
            return None
            
        # Объединяем все DataFrame
        combined_df = pd.concat(dfs, ignore_index=True)
        
        # Обрабатываем даты и сортируем
        combined_df["date"] = pd.to_datetime(combined_df["date"], format="%Y-%m-%dT%H:%M:%SZ")
        combined_df.sort_values(by='date', inplace=True)
        combined_df.reset_index(drop=True, inplace=True)
        
        return combined_df
    
    def save_processed_data(self, ticker, data, timeframe="1min"):
        """Сохранить обработанные данные в Parquet формате"""
        ticker_dir = self.get_ticker_processed_path(ticker)
        
        # Создаем имя файла с информацией о периоде данных
        if len(data) > 0:
            start_date = data['date'].min().strftime('%Y-%m-%d')
            end_date = data['date'].max().strftime('%Y-%m-%d')
            file_name = f"{ticker}_{start_date}_{end_date}.parquet"
        else:
            file_name = f"{ticker}_empty.parquet"
        
        file_path = os.path.join(ticker_dir, file_name)
        
        # Сохраняем в формате Parquet (эффективнее CSV)
        data.to_parquet(file_path, index=False)
        
        return file_path
    
    def load_processed_data(self, ticker):
        """Загрузить обработанные данные для тикера"""
        ticker_dir = self.get_ticker_processed_path(ticker)
        
        all_files = []
        for file in os.listdir(ticker_dir):
            if file.endswith(".parquet"):
                all_files.append(os.path.join(ticker_dir, file))
        
        if not all_files:
            return None
        
        # Загружаем все файлы и объединяем
        dfs = [pd.read_parquet(file) for file in all_files]
        if not dfs:
            return None
        
        combined_df = pd.concat(dfs, ignore_index=True)
        combined_df.drop_duplicates(subset=['date'], inplace=True)
        combined_df.sort_values(by='date', inplace=True)
        combined_df.reset_index(drop=True, inplace=True)
        
        return combined_df
    
    def get_missing_date_ranges(self, ticker, start_date, end_date):
        """
        Определить, какие периоды данных отсутствуют для тикера
        
        :return: Список кортежей (start_date, end_date) для отсутствующих периодов
        """
        existing_data = self.load_processed_data(ticker)
        
        start_date = pd.to_datetime(start_date)
        end_date = pd.to_datetime(end_date)
        
        if existing_data is None or len(existing_data) == 0:
            # Нет данных, нужно загрузить весь период
            return [(start_date, end_date)]
        
        # Находим пропущенные периоды
        existing_dates = existing_data['date']
        min_date = existing_dates.min()
        max_date = existing_dates.max()
        
        missing_ranges = []
        
        # Проверяем, нужно ли загрузить данные до имеющихся
        if start_date < min_date:
            missing_ranges.append((start_date, min_date))
        
        # Проверяем, нужно ли загрузить данные после имеющихся
        if end_date > max_date:
            missing_ranges.append((max_date, end_date))
        
        return missing_ranges


class TimeframeConverter:
    """Класс для преобразования временных интервалов"""
    
    @staticmethod
    def resample_ohlcv(df, timeframe):
        """
        Преобразовать минутные данные OHLCV в другой временной интервал
        
        :param df: DataFrame с минутными данными (колонки: date, open, close, min, max, volume)
        :param timeframe: Целевой временной интервал ('5min', '15min', '1h', '1d', '1w', '1M')
        :return: DataFrame с преобразованными данными
        """
        if df is None or len(df) == 0:
            return pd.DataFrame()
        
        # Проверка корректности входных данных
        required_columns = ['date', 'open', 'close', 'min', 'max', 'volume']
        if not all(col in df.columns for col in required_columns):
            raise ValueError(f"DataFrame должен содержать колонки: {required_columns}")
        
        # Устанавливаем date как индекс
        df = df.copy()
        df.set_index('date', inplace=True)
        
        # Преобразуем временной интервал
        # Правила агрегации для OHLCV данных:
        # - open: первое значение в интервале
        # - high: максимальное значение
        # - low: минимальное значение
        # - close: последнее значение
        # - volume: сумма
        
        # Маппинг имен колонок dataframe -> pandas aggregation
        agg_dict = {
            'open': 'first',
            'min': 'min',        # минимальная цена (low)
            'max': 'max',        # максимальная цена (high)
            'close': 'last',
            'volume': 'sum'
        }
        
        # Преобразуем pandas timeframe в формат ресемплирования
        resample_map = {
            '5min': '5T',
            '15min': '15T',
            '1h': 'H',
            '1d': 'D',
            '1w': 'W',
            '1M': 'M'
        }
        
        # Выбираем правило ресемплирования
        rule = resample_map.get(timeframe, '1D')  # По умолчанию дневной интервал
        
        # Выполняем ресемплирование
        resampled = df.resample(rule).agg(agg_dict)
        
        # Сбрасываем индекс для получения колонки date
        resampled.reset_index(inplace=True)
        
        return resampled
    

class TinkoffDataDownloader:
    """Класс для скачивания данных из Tinkoff API"""
    
    def __init__(self, token, storage):
        """
        Инициализация загрузчика данных
        
        :param token: API токен Tinkoff
        :param storage: Экземпляр класса DataStorage
        """
        self.url = "https://invest-public-api.tinkoff.ru/history-data"
        self.token = token
        self.storage = storage
        self.df_screener_all = None
        self.df_screener_rub = None
        self.initialize_instruments()
        
    def initialize_instruments(self):
        """Получение списка доступных инструментов"""
        with Client(token=self.token) as client:
            instruments = client.instruments
            self.df_screener_all = pd.DataFrame(
                instruments.shares(instrument_status=InstrumentStatus.INSTRUMENT_STATUS_ALL).instruments,
                columns=["name", "ticker", "uid", "figi", "isin", "lot", "currency"])
            self.df_screener_rub = self.df_screener_all[self.df_screener_all["currency"] == "rub"]
    
    def get_figi(self, ticker) -> list:
        """Получение FIGI для тикера"""
        if not self.df_screener_rub[self.df_screener_rub["ticker"] == ticker].empty:
            ticker_figi = self.df_screener_rub[self.df_screener_rub["ticker"] == ticker]
            
            if ticker_figi.shape[0] > 1:
                return list(ticker_figi["figi"])
            else:
                return [ticker_figi["figi"].iloc[0]]
        return []
        
    def get_uid(self, ticker) -> list:
        """Получение UID для тикера"""
        if not self.df_screener_rub[self.df_screener_rub["ticker"] == ticker].empty:
            ticker_figi = self.df_screener_rub[self.df_screener_rub["ticker"] == ticker]
            
            if ticker_figi.shape[0] > 1:
                return list(ticker_figi["uid"])
            else:
                return [ticker_figi["uid"].iloc[0]]
        return []
        
    def get_isin(self, ticker) -> list:
        """Получение ISIN для тикера"""
        if not self.df_screener_rub[self.df_screener_rub["ticker"] == ticker].empty:
            ticker_figi = self.df_screener_rub[self.df_screener_rub["ticker"] == ticker]
            
            if ticker_figi.shape[0] > 1:
                return list(ticker_figi["isin"])
            else:
                return [ticker_figi["isin"].iloc[0]]
        return []

    def is_figi_correct(self, figi) -> bool:
        """Проверка корректности FIGI"""
        correct_figi = False
        figi_path = os.path.join(self.storage.base_directory, "figi.txt")
        if os.path.exists(figi_path):
            with open(figi_path, 'r') as file:
                for line in file:
                    if figi in line:
                        correct_figi = True
        return correct_figi
        
    def get_correct_figi(self, figi_list) -> str:
        """Получение корректного FIGI из списка"""
        for figi in figi_list:
            if self.is_figi_correct(figi):
                return figi
        return "0"
    
    def download_for_ticker(self, ticker, start_date, end_date):
        """
        Скачать данные для тикера за указанный период
        
        :param ticker: Тикер инструмента
        :param start_date: Дата начала в формате YYYY-MM-DD
        :param end_date: Дата окончания в формате YYYY-MM-DD
        :return: DataFrame с минутными данными
        """
        # Определяем годы, за которые нужно скачать данные
        start_year = pd.to_datetime(start_date).year
        end_year = pd.to_datetime(end_date).year
        years_to_download = list(range(start_year, end_year + 1))
        
        # Получаем идентификаторы для тикера
        ticker_figi = self.get_figi(ticker)
        ticker_uid = self.get_uid(ticker)
        ticker_isin = self.get_isin(ticker)
        
        # Получаем корректный FIGI
        correct_figi = self.get_correct_figi(ticker_figi)
        
        downloaded_files = []
        
        if correct_figi != "0":
            print(f"Congratulations! {correct_figi} is a correct figi for {ticker}")
            
            # Скачиваем данные за все годы
            for year in years_to_download:
                file_path = self._download_year_data(ticker, correct_figi, year)
                if file_path:
                    downloaded_files.append(file_path)
        else:
            # Пробуем разные идентификаторы
            for instrument_list in [ticker_figi, ticker_isin, ticker_uid]:
                for instrument_id in instrument_list:
                    # Скачиваем данные за все годы
                    for year in years_to_download:
                        file_path = self._download_year_data(ticker, instrument_id, year)
                        if file_path:
                            downloaded_files.append(file_path)
            
        # Извлекаем данные из zip-файлов
        csv_files = self.storage.extract_raw_data(ticker)
        
        # Загружаем данные из CSV-файлов
        data = self.storage.load_raw_csv_files(csv_files)
        
        # Фильтруем по указанному диапазону дат
        if data is not None:
            start_date_dt = pd.to_datetime(start_date)
            end_date_dt = pd.to_datetime(end_date)
            
            data = data[(data['date'] >= start_date_dt) & (data['date'] <= end_date_dt)]
            
            # Сохраняем обработанные данные
            self.storage.save_processed_data(ticker, data)
            
            # Выводим статистику
            self._print_data_stats(ticker, data)
        
        return data
    
    def _download_year_data(self, ticker, figi, year):
        """Скачать данные за определенный год"""
        print(f"Downloading {ticker} ({figi}) for year {year}")
        
        params = {"figi": figi, "year": year, "interval": "1min"}
        headers = {"Authorization": f"Bearer {self.token}"}
        
        try:
            response = requests.get(self.url, params=params, headers=headers)
            
            if response.status_code == 200:
                # Сохраняем сырые данные
                return self.storage.store_raw_data(ticker, figi, year, response.content)
            elif response.status_code == 429:
                print("Rate limit exceeded. Sleeping for 5 seconds...")
                time.sleep(5)
                return self._download_year_data(ticker, figi, year)
            elif response.status_code == 404:
                print(f"No data found for {ticker} ({figi}) in {year}")
                return None
            else:
                print(f"Error downloading data: {response.status_code}")
                return None
                
        except Exception as e:
            print(f"Exception during download: {e}")
            return None
    
    def _print_data_stats(self, ticker, data):
        """Вывести статистику о скачанных данных"""
        if data is None or len(data) == 0:
            print(f"{ticker}: No data found")
            return
            
        print(f"\n{ticker} data statistics:")
        print(f"  - Total rows: {len(data)}")
        print(f"  - First date: {data['date'].min()}")
        print(f"  - Last date: {data['date'].max()}")

class MarketDataManager:
    """Основной класс для управления рыночными данными"""
    
    def __init__(self, token, base_directory):
        """
        Инициализация менеджера данных
        
        :param token: API токен Tinkoff
        :param base_directory: Корневая директория для хранения данных
        """
        self.storage = DataStorage(base_directory)
        self.downloader = TinkoffDataDownloader(token, self.storage)
        self.converter = TimeframeConverter()
    
    def get_data(self, ticker, start_date, end_date, timeframe="1min", force_download=False):
        """
        Получить данные для тикера с указанным временным интервалом
        
        :param ticker: Тикер инструмента
        :param start_date: Дата начала в формате YYYY-MM-DD
        :param end_date: Дата окончания в формате YYYY-MM-DD
        :param timeframe: Временной интервал ('1min', '5min', '15min', '1h', '1d', '1w', '1M')
        :param force_download: Принудительно скачать новые данные, даже если они уже есть
        :return: DataFrame с данными
        """
        # Проверяем, есть ли у нас уже данные для этого тикера
        existing_data = None if force_download else self.storage.load_processed_data(ticker)
        
        # Определяем, какие периоды данных нужно дозагрузить
        if existing_data is not None:
            missing_ranges = self.storage.get_missing_date_ranges(ticker, start_date, end_date)
            
            # Если есть пропущенные периоды, скачиваем их
            for range_start, range_end in missing_ranges:
                print(f"Downloading missing data for {ticker} from {range_start} to {range_end}")
                new_data = self.downloader.download_for_ticker(ticker, range_start.strftime('%Y-%m-%d'), 
                                                             range_end.strftime('%Y-%m-%d'))
                
                if new_data is not None and len(new_data) > 0:
                    # Объединяем с существующими данными
                    existing_data = pd.concat([existing_data, new_data], ignore_index=True)
                    existing_data.drop_duplicates(subset=['date'], inplace=True)
                    existing_data.sort_values(by='date', inplace=True)
                    existing_data.reset_index(drop=True, inplace=True)
            
            # Фильтруем по указанному диапазону дат
            start_date_dt = pd.to_datetime(start_date)
            end_date_dt = pd.to_datetime(end_date)
            data = existing_data[(existing_data['date'] >= start_date_dt) & 
                                 (existing_data['date'] <= end_date_dt)]
        else:
            # У нас нет данных для этого тикера, скачиваем все
            data = self.downloader.download_for_ticker(ticker, start_date, end_date)
        
        # Если нужен временной интервал, отличный от минутного, выполняем преобразование
        if timeframe != "1min" and data is not None and len(data) > 0:
            data = self.converter.resample_ohlcv(data, timeframe)
        
        return data
    
    def get_data_for_multiple_tickers(self, tickers, start_date, end_date, timeframe="1min", max_workers=5):
        """
        Получить данные для нескольких тикеров с использованием многопоточности
        
        :param tickers: Список тикеров
        :param start_date: Дата начала в формате YYYY-MM-DD
        :param end_date: Дата окончания в формате YYYY-MM-DD
        :param timeframe: Временной интервал ('1min', '5min', '15min', '1h', '1d', '1w', '1M')
        :param max_workers: Максимальное количество потоков
        :return: Словарь {ticker: DataFrame}
        """
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ticker = {
                executor.submit(self.get_data, ticker, start_date, end_date, timeframe): ticker 
                for ticker in tickers
            }
            
            for future in future_to_ticker:
                ticker = future_to_ticker[future]
                try:
                    data = future.result()
                    results[ticker] = data
                    
                    # Выводим статистику
                    if data is not None and len(data) > 0:
                        print(f"\n{ticker} data:")
                        print(f"  - Timeframe: {timeframe}")
                        print(f"  - Total rows: {len(data)}")
                        print(f"  - First date: {data['date'].min()}")
                        print(f"  - Last date: {data['date'].max()}")
                    else:
                        print(f"\n{ticker}: No data available")
                        
                except Exception as e:
                    print(f"Error processing {ticker}: {e}")
                    results[ticker] = None
        
        return results