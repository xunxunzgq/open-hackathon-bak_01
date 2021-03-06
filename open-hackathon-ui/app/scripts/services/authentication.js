/*
 * Copyright (c) Microsoft Open Technologies (Shanghai) Co. Ltd.  All rights reserved.
 *
 * The MIT License (MIT)
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

'use strict';

/**
 * Created by v-boguan on 2015/5/3.
 */

angular.module('oh.app')
  .factory('Authentication', function Authentication($q, $cookies, $stateParams, state, API) {
    var authenticatedUser = undefined;
    var token = '';


    /**
     * Get user information
     * @method getUser
     * @returns {{avatar_url: string, create_time: string, email: {create_time: number, email: string, id: number, name: string, primary_email: number, update_time: null, user_id: number, verified: number}[], id: number, last_login_time: string, name: string, nickname: string, online: number}}
     */
    function getUser() {
      token = $cookies.get('token') || '';
      if (!authenticatedUser && token.length > 0) {
        var user = $cookies.get('User');
        authenticatedUser = user ? JSON.parse(user) : user;
        //API.user.get().then(function (res) {
        //  if (!res.data.error) {
        //    authenticatedUser = res.data;
        //  }
        //});
      }
      return authenticatedUser;
    }

    function logout() {
      API.user.login.delete().then(function () {
        token = undefined;
        authenticatedUser = undefined;
        $cookies.remove('token');
        window.location.reload();
      });
    }

    /**
     * hackathon Page load
     * @method hackathon
     * @param {function}
     */
    function hackathon(callback) {
      var hackathon_name = $stateParams.hackathon_name || config.name;
      var user = this.getUser();
      var hackathon_name = $stateParams.hackathon_name || config.name;
      if (user) {
        API.user.registration.get({header: {hackathon_name: hackathon_name}}, function (data) {
          if (data.error) {
            state.go('index');
          } else {
            if (data.hackathon.status != 1) {
              state.go('index.home');
            } else {
              if (data.registration) {
                if (data.hackathon.basic_info.auto_approve) {
                  callback(data);
                } else if (data.registration.status == 0 || data.registration.status == 2) {
                  state.go('index.register', {hackathon_name: hackathon_name});
                } else if (data.registration.status == 3 && data.hackathon.basic_info.auto_approve == 0) {
                  state.go('index.register', {hackathon_name: hackathon_name});
                } else if (!data.experiment) {
                  state.go('index.settings', {hackathon_name: hackathon_name});
                } else {
                  callback(data);
                }
              } else {
                state.go('index.register');
              }
            }
          }
        });
      } else {
        state.go('index');
      }
    }

    /**
     * settings Page load
     * @method settings
     * @param {function}
     */
    function settings(callback) {
      var hackathon_name = $stateParams.hackathon_name || config.name;
      var user = this.getUser();
      var hackathon_name = $stateParams.hackathon_name || config.name;
      if (user) {
        API.user.registration.get({header: {hackathon_name: hackathon_name}}, function (data) {
          if (data.error) {
            state.go('index');
          } else {
            if (data.hackathon.status != 1) {
              state.go('index.home');
            } else {
              if (data.registration) {
                if (data.hackathon.basic_info.auto_approve) {
                  callback(data);
                } else if (data.registration.status == 0 || data.registration.status == 2) {
                  state.go('index.register', {hackathon_name: hackathon_name});
                } else if (data.registration.status == 3) {
                  state.go('index.register', {hackathon_name: hackathon_name});
                } else if (data.experiment) {
                  state.go('hackathon', {hackathon_name: hackathon_name});
                } else {
                  callback(data);
                }
              } else {
                state.go('index.register', {hackathon_name: hackathon_name});
              }
            }
          }
        });
      } else {
        state.go('index');
      }
    }

    return {
      logout: logout,
      getUser: getUser,
      hackathon: hackathon,
      settings: settings
    }
  });




