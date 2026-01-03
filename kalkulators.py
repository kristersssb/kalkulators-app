# %%

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

import pvlib
from pvlib.modelchain import ModelChain
from pvlib.location import Location
from pvlib.pvsystem import PVSystem
from pvlib.temperature import TEMPERATURE_MODEL_PARAMETERS

from entsoe import EntsoePandasClient

import streamlit as st 
import streamlit as st 
import folium
from streamlit_folium import st_folium

st.set_page_config(layout="wide")

st.title("Saules paneļu atmaksas kalkulators")

col1, col2 = st.columns(2)

with col1:
    st.subheader("Izvēlieties savas mājas atrašanās vietu kartē")
    m = folium.Map(location=[56.9496, 24.1052], zoom_start=8)
    map_data = st_folium(m, width=1200, height=500)

    latitude = 56.9496
    longitude = 24.1052

    if map_data and map_data.get("last_clicked"):
        clicked_point = map_data["last_clicked"]
        latitude = clicked_point["lat"]
        longitude = clicked_point["lng"]
        st.success(f"Atrašanās vieta izvēlēta: Platums={latitude:.4f}, Garums={longitude:.4f}")
    else:
        st.info("Noklikšķiniet uz kartes, lai izvēlētos precīzu atrašanās vietu.")

with col2:
    st.subheader("Lietotāja izvēle")
    col1, col2 = st.columns(2)
    with col1:
        majas_tips = st.radio(
            "Izvēlies mājas tipu:",
            ['Standarta', 'Ar siltumsūkni'],
            index = 0
        )
    with col2:
        total_annual_consumption_kwh = st.number_input("Cik ir jūsu mājas gada patēriņš kWh?", min_value=0, value=5000)

    col1, col2 = st.columns(2)
    with col1:
        akumulators = st.radio(
            "Vai vēlies izmantot akumulatoru?",
            ['Jā', 'Nē'],
            index = 1
        )

    with col2:
        if akumulators == 'Jā':
            akumulatora_ietilpiba_kwh = st.number_input("Cik liela ir akumulatora ietilpība kWh?", min_value=0)
        else:
            akumulatora_ietilpiba_kwh = st.number_input("Cik liela ir akumulatora ietilpība kWh?", disabled=True)
            arbitrage = 'Nē'
            
    if akumulators == 'Jā':
        arbitrage = st.radio(
            "Vai vēlaties lai cenu rēķina ar arbitrāžas iespēju?",
            ['Jā', 'Nē'],
            index = 1,
        )
    else: st.radio(
        "Vai vēlaties lai cenu rēķina ar arbitrāžas iespēju?",
        ['Jā', 'Nē'],
        index = 1,
        disabled=True
        )


location = Location(latitude=latitude, longitude=longitude, tz='Europe/Riga', altitude=10)

# Definējam diennakts profilu (tiek izmantots vairākos scenārijos)
hourly_profile_shape = np.array([0.3, 0.2, 0.2, 0.2, 0.3, 0.4, 0.6, 0.8, 0.7, 0.6, 0.5, 0.5, 0.4, 0.4, 0.5, 0.6, 0.7, 0.9, 1.0, 1.0, 0.9, 0.7, 0.5, 0.4])
hourly_profile_shape = hourly_profile_shape / hourly_profile_shape.sum()

# Definējam laika periodu (2023. gads, stundas)
times = pd.date_range(start="2023-01-01 00:00", end="2023-12-31 23:00", freq='h', tz='Europe/Riga')

# Pārbaudām, vai sesijas atmiņā vēl nav nepieciešamo mainīgo
if "aprekins_palaists" not in st.session_state:
    st.session_state.aprekins_palaists = False
    
if "rezultati_df" not in st.session_state:
    st.session_state.rezultati_df = pd.DataFrame()
    
if "rezultats" not in st.session_state:
    st.session_state["rezultats"] = None
    
# -----------------------------

if st.button("Aprēķināt"):
    st.session_state.aprekins_palaists = True
    with st.spinner("Notiek datu ielāde un aprēķini... Lūdzu, uzgaidiet."):



        # Definējam parametrus katram mājas tipam
        if majas_tips == 'Standarta':
            monthly_consumption_points = {
                '2023-01-15': 412, '2023-02-15': 401, '2023-03-15': 376, '2023-04-15': 311,
                '2023-05-15': 307, '2023-06-15': 281, '2023-07-15': 295, '2023-08-15': 297,
                '2023-09-15': 284, '2023-10-15': 334, '2023-11-15': 366, '2023-12-15': 422
            }
            chart_title = "Standarta mājas patēriņš"
        elif majas_tips == 'Ar siltumsūkni':
            monthly_consumption_points = {
                '2023-01-15': 1800, '2023-02-15': 1200, '2023-03-15': 1000, '2023-04-15': 500,
                '2023-05-15': 350,  '2023-06-15': 250,  '2023-07-15': 250,  '2023-08-15': 300,
                '2023-09-15': 400,  '2023-10-15': 750,  '2023-11-15': 1200, '2023-12-15': 1500
            }
            chart_title = "Mājas ar siltumsūkni patēriņš"
        else:
            raise ValueError("Nezināms mājas tips. Izvēlies 'standarta' vai 'siltumsuknis'.")

        # Sezonālais profils
        monthly_series = pd.Series(monthly_consumption_points)
        monthly_series.index = pd.to_datetime(monthly_series.index)
        daily_profile_with_gaps = monthly_series.reindex(pd.date_range(start='2023-01-01', end='2023-12-31', freq='D'))
        interpolated_daily = daily_profile_with_gaps.interpolate(method='linear').bfill().ffill()
        daily_factors = interpolated_daily / interpolated_daily.sum()
        daily_consumption_kwh = daily_factors * total_annual_consumption_kwh

        # Diennakts profilis
        hourly_consumption_list = []
        for day_consumption in daily_consumption_kwh:
            hourly_consumption_list.extend(day_consumption * hourly_profile_shape)
        household_consumption_hourly = pd.DataFrame(data={'consumption_kwh': hourly_consumption_list}, index=times)

        # Saules paneļu un invertora parametri
        sandia_modules = pvlib.pvsystem.retrieve_sam('CECMod')
        cec_inverters = pvlib.pvsystem.retrieve_sam('CECInverter')

        module = sandia_modules['Jinko_Solar_Co___Ltd_JKM410M_72HL_V']
        inverter = cec_inverters['Huawei_Technologies_Co___Ltd___SUN2000_10KTL_USL0__240V_']
        temperature_parameters = TEMPERATURE_MODEL_PARAMETERS['sapm']['open_rack_glass_polymer']

        panelu_skaits = int(np.ceil((total_annual_consumption_kwh / 950) / 0.41))  # Aptuveni 950 kWh/kW gadā un 410 W panelis
        system = PVSystem(surface_tilt=30, surface_azimuth=225,
                        module_parameters=module, inverter_parameters=inverter,
                        temperature_model_parameters=temperature_parameters,
                        modules_per_string = panelu_skaits, strings_per_inverter = 1) # x paneļi virknē un y virknes uz invertoru

        tmy_data, _ = pvlib.iotools.get_pvgis_tmy(
        latitude=latitude, 
        longitude=longitude, 
        outputformat='csv', 
        map_variables=True
        )
        tmy_data.index = tmy_data.index.tz_convert('Europe/Riga')

        modelchain = ModelChain(system, location, aoi_model='ashrae', transposition_model='perez')
        modelchain.run_model(tmy_data)

        # Iegūstam stundas jaudu (W) no modeļa rezultātiem
        ac_power_w = modelchain.results.ac
        ac_energy_kwh = ac_power_w / 1000
        ac_energy_kwh[ac_energy_kwh < 0] = 0
        ac_energy_kwh.index = times

        paterins = household_consumption_hourly['consumption_kwh']
        razosana = ac_energy_kwh


        # Izveidojam tukšus sarakstus rezultātiem
        paspaterins_list = []
        uzkrajums_pardosanai_list = [] # Pārpalikums, ko var pārdot
        pirkts_no_tikla_list = [] # Iztrūkums, kas jāpērk

        # ENTSO-E biržas cenas
        api_key = st.secrets["auth_api_key"]
        client = EntsoePandasClient(api_key=api_key)
        country_code = 'LV'
        start_entsoe = pd.Timestamp('2023-01-01 00:00', tz='Europe/Riga')
        end_entsoe = pd.Timestamp('2023-12-31 23:00', tz='Europe/Riga')
        prices = client.query_day_ahead_prices(country_code, start=start_entsoe, end=end_entsoe)
        prices = prices.tz_convert('Europe/Riga')
        prices_kwh = (prices / 1000).reindex(times).fillna(method='ffill')

        # Saraksti priekš arbitrāžas
        daily_prices = prices_kwh.groupby(prices_kwh.index.date)
        ladet_stundas = []
        izladet_stundas = []
        
        
        # Izmaksu un ienākumu saraksti
        izmaksas_list = []
        ienakumi_list = []
        ietaupijums_list = []

        max_uzlade = akumulatora_ietilpiba_kwh
        if akumulators == "Jā" and arbitrage == "Jā":
            max_uzlade = akumulatora_ietilpiba_kwh / 3
        akumulatora_stavoklis_list = []
        pasreizeja_uzlade = 0
        
        for i, (p, r) in enumerate(zip(paterins, razosana)):
            pasreizejais_laiks = paterins.index[i]
            price = prices_kwh.iloc[i] if not pd.isna(prices_kwh.iloc[i]) else 0

            # Dienas plānošana (pusnaktī)
            if pasreizejais_laiks.hour == 0:
            # Paņemam šīs dienas cenas
                dienas_cenu_saraksts = daily_prices.get_group(pasreizejais_laiks.date())
                # Atrodam 4 stundas ar zemāko cenu
                letakas_stundas_info = dienas_cenu_saraksts.nsmallest(3)
                ladet_stundas = letakas_stundas_info.index.tolist() # Saglabājam to laikus
                
                # Atrodam dienas dārgāko stundu
                dargaka_stunda = dienas_cenu_saraksts.nlargest(3)
                izladet_stundas = dargaka_stunda.index.tolist()
            
            
            # --- STUNDAS APRĒĶINS ---
            pirkts = 0 # cik kWh jāpērk no tīkla šajā stundā
            izmaksas = 0 # cik EUR jāmaksā šajā stundā
            pardosanai = 0 # cik kWh var pārdot tīklam šajā stundā
            ienakumi = 0 # cik EUR ienākumi no pārdošanas tīklam šajā stundā
            pardosanai_akumulators = 0 # cik kWh pārdodam no akumulatora šajā stundā
            if r > p:  
                # Ražošana ir lielāka par patēriņu
                paspaterins = p
                parpalikums = r - p
                # Nosakām, cik varam ielādēt akumulatorā no saules
                briva_vieta = akumulatora_ietilpiba_kwh - pasreizeja_uzlade
                ieladet = min(parpalikums, briva_vieta, max_uzlade)
                pasreizeja_uzlade += ieladet
                briva_vieta -= ieladet
                pardosanai = parpalikums - ieladet
                ienakumi = pardosanai * price
            else:
                iztrukums = p - r
                panemt_no_akumulatora = min(pasreizeja_uzlade, iztrukums)
                pasreizeja_uzlade -= panemt_no_akumulatora
                pirkts = iztrukums - panemt_no_akumulatora
                paspaterins = p - pirkts
                izmaksas = (pirkts * 0.0479) + (pirkts * price)

            # Arbitrāžas uzlāde
            briva_vieta = akumulatora_ietilpiba_kwh - pasreizeja_uzlade
            uzlade_no_tikla = 0
            if arbitrage == "Jā" and pasreizejais_laiks in ladet_stundas and briva_vieta > 0:
                uzlade_no_tikla = min(briva_vieta, max_uzlade)
                pasreizeja_uzlade += uzlade_no_tikla
                pirkts += uzlade_no_tikla
                izmaksas += uzlade_no_tikla * (0.0479 + price)

            # Arbitrāžas pārdošana
            if arbitrage == "Jā" and pasreizejais_laiks in izladet_stundas and pasreizeja_uzlade > 0:
                pardosanai_akumulators = min(pasreizeja_uzlade, max_uzlade)
                pasreizeja_uzlade -= pardosanai_akumulators
                ienakumi += pardosanai_akumulators * price
                pardosanai += pardosanai_akumulators

            ietaupijums_stunda = paspaterins * (0.0479 + price)
            
            # Pievienojam aprēķinātās vērtības sarakstiem
            paspaterins_list.append(paspaterins)
            uzkrajums_pardosanai_list.append(pardosanai)
            pirkts_no_tikla_list.append(pirkts)
            izmaksas_list.append(izmaksas)
            ienakumi_list.append(ienakumi)
            ietaupijums_list.append(ietaupijums_stunda)
            akumulatora_stavoklis_list.append(pasreizeja_uzlade)
        df_aprekini = pd.DataFrame({
        'cena_eur_kwh': prices_kwh.values,
        'patēriņš_kwh': paterins.values,
        'ražošana_kwh': razosana.values,
        'pašpatēriņš_kwh': paspaterins_list,
        'pārdots_tīklam_kwh': uzkrajums_pardosanai_list,
        'pirkts_no_tīkla_kwh': pirkts_no_tikla_list,
        'izmaksas_eur': izmaksas_list,
        'ienākumi_eur': ienakumi_list,
        'ietaupījums_eur': ietaupijums_list,
        'akumulatora_stāvoklis_kwh': akumulatora_stavoklis_list
        }, index=paterins.index)
        df_aprekini.to_csv("aprekini.csv")
        st.session_state["rezultats"] = df_aprekini

        
        total_izmaksas = df_aprekini['izmaksas_eur'].sum()
        total_ienakumi = df_aprekini['ienākumi_eur'].sum()
        total_ietaupijums = df_aprekini['ietaupījums_eur'].sum()
        kopejais_paspaterins = df_aprekini['pašpatēriņš_kwh'].sum() 
        print(f"Kopējās izmaksas: {total_izmaksas:.2f} EUR")
        print(f"Kopējie ienākumi: {total_ienakumi:.2f} EUR")
        print(f"Kopējais ietaupījums pašpatēriņā: {total_ietaupijums:.2f} EUR")
        print(f"Kopējais gada pašpatēriņš: {kopejais_paspaterins:.0f} kWh")



if st.session_state["rezultats"] is not None:
    df_aprekini = st.session_state["rezultats"]

    parvades_tarifs = 0.0479
    total_izmaksas = df_aprekini['izmaksas_eur'].sum()
    total_ienakumi = df_aprekini['ienākumi_eur'].sum()
    total_ietaupijums = df_aprekini['ietaupījums_eur'].sum()
    kopejais_paspaterins = df_aprekini['pašpatēriņš_kwh'].sum()

    cost_without_panels = (df_aprekini['patēriņš_kwh'] * (df_aprekini['cena_eur_kwh'] + parvades_tarifs)).sum()
    final_bill_with_panels = total_izmaksas - total_ienakumi
    total_yearly_savings = cost_without_panels - final_bill_with_panels
    net_cash_flow_with_grid = total_ienakumi - total_izmaksas

    st.subheader("Kopsavilkums")

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Kopējās izmaksas pērkot no tīkla", f"{total_izmaksas:,.2f} €")
    with col2:
        st.metric("Kopējie ienākumi pārdodot tīklam", f"{total_ienakumi:,.2f} €")
    with col3:
        st.metric("Gada rēķins", f"{net_cash_flow_with_grid:,.2f} €")
    with col4:
        st.metric("Ietaupījums pašpatēriņā", f"{total_ietaupijums:,.2f} €")
    with col5:
        st.metric("Kopējais gada ietaupījums", f"{total_yearly_savings:,.2f} €")
    
    st.subheader("Mēneša kopsavilkuma grafiki")

    col1, col2, col3 = st.columns(3)

    monthly_summary = df_aprekini.resample('ME').sum()
    menesu_nosaukumi = ['Jan', 'Feb', 'Mar', 'Apr', 'Mai', 'Jūn',
                    'Jūl', 'Aug', 'Sep', 'Okt', 'Nov', 'Dec']

    with col1:
        # 1. grafiks: ražošana / patēriņš
        fig1, ax1 = plt.subplots(figsize=(12, 6))
        monthly_summary[['ražošana_kwh', 'patēriņš_kwh']].plot(kind='bar', ax=ax1, color=['green', 'blue'])
        ax1.set_title("Mēneša enerģijas bilance", fontsize=16)
        ax1.set_ylabel("Enerģija (kWh)", fontsize=12)
        ax1.set_xticklabels(menesu_nosaukumi, rotation=45)
        ax1.legend(["Ražošana", "Patēriņš"])
        ax1.grid(axis='y', linestyle='--')
        st.pyplot(fig1)

    with col2:
        # === 2. grafiks: pirkts / pārdots
        fig2, ax2 = plt.subplots(figsize=(12, 6))
        monthly_summary[['pārdots_tīklam_kwh', 'pirkts_no_tīkla_kwh']].plot(kind='bar', ax=ax2, color=['lightgreen', 'red'])
        ax2.set_title("Tīkla izmantošana pa mēnešiem", fontsize=16)
        ax2.set_ylabel("Enerģija (kWh)", fontsize=12)
        ax2.set_xticklabels(menesu_nosaukumi, rotation=45)
        ax2.legend(["Pārdots tīklam", "Pirkts no tīkla"])
        ax2.grid(axis='y', linestyle='--')
        st.pyplot(fig2)

    with col3:
        # === 3. grafiks: ienākumi / izmaksas
        fig3, ax3 = plt.subplots(figsize=(12, 6))
        monthly_summary[['ienākumi_eur', 'izmaksas_eur']].plot(kind='bar', ax=ax3, color=['gold', 'salmon'])
        ax3.set_title("Finanšu plūsma pa mēnešiem", fontsize=16)
        ax3.set_ylabel("Summa (EUR)", fontsize=12)
        ax3.set_xticklabels(menesu_nosaukumi, rotation=45)
        ax3.legend(["Ienākumi", "Izmaksas"])
        ax3.grid(axis='y', linestyle='--')
        st.pyplot(fig3)
        
# %%

