#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from flask import Flask, make_response, request
from jinja2 import Environment, FileSystemLoader
from requests import get
# from waitress import serve
# from paste.translogger import TransLogger
from pathlib import Path
import os, json, argparse, pickle
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
from emhass.command_line import set_input_data_dict
from emhass.command_line import perfect_forecast_optim, dayahead_forecast_optim, naive_mpc_optim
from emhass.command_line import publish_data
from emhass.utils import get_root

# Define the Flask instance
app = Flask(__name__, static_url_path='/static')

def get_injection_dict(df, plot_size = 1366):
    # Create plots
    fig = px.line(df, title='Systems powers and optimization cost results', 
                  template='seaborn', width=plot_size, height=0.75*plot_size)
    fig.update_traces(line_shape="vh")
    # Get full path to image
    image_path_0 = fig.to_html(full_html=False, default_width='75%')
    # The tables
    table1 = df.reset_index().to_html(classes='mystyle', index=False)
    # The dict of plots
    injection_dict = {}
    injection_dict['title'] = '<h2>EMHASS optimization results</h2>'
    injection_dict['subsubtitle2'] = '<h4>Plotting latest optimization results</h4>'
    injection_dict['figure_0'] = image_path_0
    injection_dict['subsubtitle1'] = '<h4>Last run optimization results table</h4>'
    injection_dict['table1'] = table1
    return injection_dict

def build_params(params, options):
    # Updating variables in retrieve_hass_conf
    params['retrieve_hass_conf'][0]['freq'] = options['optimization_time_step']
    params['retrieve_hass_conf'][1]['days_to_retrieve'] = options['historic_days_to_retrieve']
    params['retrieve_hass_conf'][2]['var_PV'] = options['sensor_power_photovoltaics']
    params['retrieve_hass_conf'][3]['var_load'] = options['sensor_power_load_no_var_loads']
    params['retrieve_hass_conf'][6]['var_replace_zero'] = [options['sensor_power_photovoltaics']]
    params['retrieve_hass_conf'][7]['var_interp'] = [options['sensor_power_photovoltaics'], options['sensor_power_load_no_var_loads']]
    # Updating variables in optim_conf
    params['optim_conf'][0]['set_use_battery'] = options['set_use_battery']
    params['optim_conf'][2]['num_def_loads'] = options['number_of_deferrable_loads']
    params['optim_conf'][3]['P_deferrable_nom'] = [i['nominal_power_of_deferrable_loads'] for i in options['list_nominal_power_of_deferrable_loads']]
    params['optim_conf'][4]['def_total_hours'] = [i['operating_hours_of_each_deferrable_load'] for i in options['list_operating_hours_of_each_deferrable_load']]
    params['optim_conf'][5]['treat_def_as_semi_cont'] = [i['treat_deferrable_load_as_semi_cont'] for i in options['list_treat_deferrable_load_as_semi_cont']]
    params['optim_conf'][6]['set_def_constant'] = [False for i in range(len(params['optim_conf'][3]['P_deferrable_nom']))]
    start_hours_list = [i['peak_hours_periods_start_hours'] for i in options['list_peak_hours_periods_start_hours']]
    end_hours_list = [i['peak_hours_periods_end_hours'] for i in options['list_peak_hours_periods_end_hours']]
    num_peak_hours = len(start_hours_list)
    list_hp_periods_list = [{'period_hp_'+str(i+1):[{'start':start_hours_list[i]},{'end':end_hours_list[i]}]} for i in range(num_peak_hours)]
    params['optim_conf'][10]['list_hp_periods'] = list_hp_periods_list
    params['optim_conf'][11]['load_cost_hp'] = options['load_peak_hours_cost']
    params['optim_conf'][12]['load_cost_hc'] = options['load_offpeak_hours_cost']
    params['optim_conf'][14]['prod_sell_price'] = options['photovoltaic_production_sell_price']
    params['optim_conf'][15]['set_total_pv_sell'] = options['set_total_pv_sell']
    # Updating variables in plant_conf
    params['plant_conf'][0]['P_grid_max'] = options['maximum_power_from_grid']
    params['plant_conf'][1]['module_model'] = [i['pv_module_model'] for i in options['list_pv_module_model']]
    params['plant_conf'][2]['inverter_model'] = [i['pv_inverter_model'] for i in options['list_pv_inverter_model']]
    params['plant_conf'][3]['surface_tilt'] = [i['surface_tilt'] for i in options['list_surface_tilt']]
    params['plant_conf'][4]['surface_azimuth'] = [i['surface_azimuth'] for i in options['list_surface_azimuth']]
    params['plant_conf'][5]['modules_per_string'] = [i['modules_per_string'] for i in options['list_modules_per_string']]
    params['plant_conf'][6]['strings_per_inverter'] = [i['strings_per_inverter'] for i in options['list_strings_per_inverter']]
    params['plant_conf'][7]['Pd_max'] = options['battery_discharge_power_max']
    params['plant_conf'][8]['Pc_max'] = options['battery_charge_power_max']
    params['plant_conf'][9]['eta_disch'] = options['battery_discharge_efficiency']
    params['plant_conf'][10]['eta_ch'] = options['battery_charge_efficiency']
    params['plant_conf'][11]['Enom'] = options['battery_nominal_energy_capacity']
    params['plant_conf'][12]['SOCmin'] = options['battery_minimum_state_of_charge']
    params['plant_conf'][13]['SOCmax'] = options['battery_maximum_state_of_charge']
    params['plant_conf'][14]['SOCtarget'] = options['battery_target_state_of_charge']
    # The params dict
    params['params_secrets'] = params_secrets
    params['passed_data'] = {'pv_power_forecast':None,'load_power_forecast':None,'load_cost_forecast':None,'prod_price_forecast':None}
    return params

@app.route('/')
def index():
    app.logger.info("EMHASS server online, serving index.html...")
    # Load HTML template
    file_loader = FileSystemLoader(base_path+'/templates')
    env = Environment(loader=file_loader)
    template = env.get_template('index.html')
    # Load cache dict
    with open(base_path+'/data/injection_dict.pkl', "rb") as fid:
        injection_dict = pickle.load(fid)
    if injection_dict is None:
        return make_response(template.render(injection_dict={}))
    else:
        return make_response(template.render(injection_dict=injection_dict))

@app.route('/action/<action_name>', methods=['POST'])
def action_call(action_name):
    with open(base_path+'/data/params.pkl', "rb") as fid:
        params = pickle.load(fid)
    runtimeparams = request.get_json(force=True)
    params = json.dumps(params)
    input_data_dict = set_input_data_dict(config_path, base_path, costfun, 
        params, runtimeparams, action_name, app.logger)
    if action_name == 'publish-data':
        app.logger.info("Publishing data...")
        _ = publish_data(input_data_dict, app.logger)
        msg = f'EMHASS >> Action publish-data executed... \n'
        return make_response(msg, 201)
    elif action_name == 'perfect-optim':
        app.logger.info("Performing perfect optimization...")
        opt_res = perfect_forecast_optim(input_data_dict, app.logger)
        injection_dict = get_injection_dict(opt_res)
        with open(base_path+'/data/injection_dict.pkl', "wb") as fid:
            pickle.dump(injection_dict, fid)
        msg = f'EMHASS >> Action perfect-optim executed... \n'
        return make_response(msg, 201)
    elif action_name == 'dayahead-optim':
        app.logger.info("Performing dayahead optimization...")
        opt_res = dayahead_forecast_optim(input_data_dict, app.logger)
        injection_dict = get_injection_dict(opt_res)
        with open(base_path+'/data/injection_dict.pkl', "wb") as fid:
            pickle.dump(injection_dict, fid)
        msg = f'EMHASS >> Action dayahead-optim executed... \n'
        return make_response(msg, 201)
    elif action_name == 'naive-mpc-optim':
        app.logger.info("Performing naive MPC optimization...")
        opt_res = naive_mpc_optim(input_data_dict, app.logger)
        injection_dict = get_injection_dict(opt_res)
        with open(base_path+'/data/injection_dict.pkl', "wb") as fid:
            pickle.dump(injection_dict, fid)
        msg = f'EMHASS >> Action naive-mpc-optim executed... \n'
        return make_response(msg, 201)
    else:
        app.logger.error("ERROR: passed action is not valid")
        msg = f'EMHASS >> ERROR: Passed action is not valid... \n'
        return make_response(msg, 400)

if __name__ == "__main__":
    # Parsing arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', type=str, help='The URL to your Home Assistant instance, ex the external_url in your hass configuration')
    parser.add_argument('--key', type=str, help='Your access key. If using EMHASS in standalone this should be a Long-Lived Access Token')
    parser.add_argument('--add_on', type=bool, default=True, help='Define if we are usinng EMHASS with the add-on or in standalone mode')
    args = parser.parse_args()
    
    # Define the paths
    if args.add_on:
        OPTIONS_PATH = "/data/options.json"
        options_json = Path(OPTIONS_PATH)
        CONFIG_PATH = "/usr/src/config_emhass.json"
        config_path = Path(CONFIG_PATH)
        base_path = str(config_path.parent)
        url = args.url
        key = args.key
    else:
        OPTIONS_PATH = "/app/config_emhass.json"
        options_json = Path(OPTIONS_PATH)
        CONFIG_PATH = "/app/config_emhass.json"
        config_path = Path(CONFIG_PATH)
        base_path = str(config_path.parent)
        url = os.getenv('LOCAL_URL', default='0.0.0.0')
        key = os.getenv('LOCAL_KEY', default='123456')
        
    # Read options info
    if options_json.exists():
        with options_json.open('r') as data:
            options = json.load(data)
    else:
        app.logger.error("options.json does not exists")

    # Read example config file
    if config_path.exists():
        with config_path.open('r') as data:
            config = json.load(data)
        retrieve_hass_conf = config['retrieve_hass_conf']
        optim_conf = config['optim_conf']
        plant_conf = config['plant_conf']
    else:
        app.logger.error("config_emhass.json does not exists")

    params = {}
    params['retrieve_hass_conf'] = retrieve_hass_conf
    params['optim_conf'] = optim_conf
    params['plant_conf'] = plant_conf
    with open(base_path+'/data/params.pkl', "wb") as fid:
        pickle.dump(params, fid)

    # Initialize this global dict
    opt_res = pd.read_csv(base_path+'/data/opt_res_dayahead_latest.csv', index_col='timestamp')
    injection_dict = get_injection_dict(opt_res)
    with open(base_path+'/data/injection_dict.pkl', "wb") as fid:
        pickle.dump(injection_dict, fid)
    
    if args.add_on:
        # The cost function
        costfun = options['costfun']
        # Some data from options
        web_ui_url = options['web_ui_url']
        url_from_options = options['hass_url']
        if url_from_options == 'empty':
            hass_url = args.url
            url = hass_url+"/config"
        else:
            hass_url = url_from_options
            url = hass_url+"/api/config"
        token_from_options = options['long_lived_token']
        if token_from_options == 'empty':
            long_lived_token = args.key
        else:
            long_lived_token = token_from_options
        headers = {
            "Authorization": "Bearer " + long_lived_token,
            "content-type": "application/json"
        }
        response = get(url, headers=headers)
        config_hass = response.json()
        params_secrets = {
            'hass_url': hass_url,
            'long_lived_token': long_lived_token,
            'time_zone': config_hass['time_zone'],
            'lat': config_hass['latitude'],
            'lon': config_hass['longitude'],
            'alt': config_hass['elevation']
        }
    else:
        pass
        
    # Build params
    with open(base_path+'/data/params.pkl', "rb") as fid:
        params = pickle.load(fid)
    params = build_params(params, options)
    with open(base_path+'/data/params.pkl', "wb") as fid:
        pickle.dump(params, fid)

    # Launch server
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host=web_ui_url, port=port)
    #serve(TransLogger(app, setup_console_handler=True), host=web_ui_url, port=port)